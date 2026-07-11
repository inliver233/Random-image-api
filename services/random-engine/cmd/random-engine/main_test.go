package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSnapshotAndRandomPick(t *testing.T) {
	st := &engineState{
		revision: "empty",
		byID:     map[int64]int{},
		tagIndex: map[string]map[int64]struct{}{},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/v1/admin/snapshot", func(w http.ResponseWriter, r *http.Request) { handleSnapshot(w, r, st) })
	mux.HandleFunc("/v1/pick", func(w http.ResponseWriter, r *http.Request) { handlePick(w, r, st) })
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		st.mu.RLock()
		defer st.mu.RUnlock()
		writeJSON(w, 200, healthResponse{OK: true, Service: "random-engine", IndexSize: len(st.byKey), SnapshotRevision: st.revision})
	})

	// empty pick
	req := httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(`{"filters":{},"strategy":"random","limit":1}`))
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("status %d", rr.Code)
	}
	var empty pickResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &empty); err != nil {
		t.Fatal(err)
	}
	if empty.Code != "NO_MATCH" {
		t.Fatalf("want NO_MATCH got %s", empty.Code)
	}

	// snapshot: status as int (DB style)
	body := map[string]any{
		"revision": "r1",
		"images": []map[string]any{
			{"id": 1, "illust_id": 100, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.1, "x_restrict": 0, "bookmark_count": 10, "view_count": 100, "tag_names": []string{"cat"}},
			{"id": 2, "illust_id": 200, "page_index": 0, "ext": "png", "status": 1, "random_key": 0.5, "x_restrict": 1, "bookmark_count": 50, "view_count": 200, "tag_names": []string{"dog"}},
			{"id": 3, "illust_id": 300, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.9, "x_restrict": 0, "bookmark_count": 5, "view_count": 50, "tag_names": []string{"cat", "cute"}},
			{"id": 4, "illust_id": 400, "page_index": 0, "ext": "jpg", "status": 2, "random_key": 0.2, "x_restrict": 0, "bookmark_count": 99, "view_count": 999, "tag_names": []string{"cat"}},
		},
	}
	raw, _ := json.Marshal(body)
	req = httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("snapshot status %d body %s", rr.Code, rr.Body.String())
	}
	if st.byKey == nil || len(st.byKey) != 3 {
		t.Fatalf("index size want 3 got %d", len(st.byKey))
	}

	// filter r18=0 strict should exclude id=2
	req = httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(`{"filters":{"r18":0,"r18_strict":1},"strategy":"random","limit":10,"seed":"fixed"}`))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	var resp pickResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Code != "OK" || len(resp.Items) == 0 {
		t.Fatalf("unexpected %#v", resp)
	}
	for _, it := range resp.Items {
		if it.ID == 2 || it.ID == 4 {
			t.Fatalf("filter leaked id %d", it.ID)
		}
	}

	// included tag cat + quality best
	req = httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(`{"filters":{"r18":2,"included_tags":["cat"]},"strategy":"quality","quality":{"samples":3,"pick_mode":"best"},"limit":1,"debug":true}`))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Code != "OK" || len(resp.Items) != 1 {
		t.Fatalf("tag pick failed: %#v", resp)
	}
	if resp.Items[0].ID != 1 && resp.Items[0].ID != 3 {
		t.Fatalf("unexpected id %d", resp.Items[0].ID)
	}
}
