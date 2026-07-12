package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"math"
	"testing"
)

func testMux(st *engineState) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/admin/snapshot", func(w http.ResponseWriter, r *http.Request) { handleSnapshot(w, r, st) })
	mux.HandleFunc("/v1/admin/events", func(w http.ResponseWriter, r *http.Request) { handleEvents(w, r, st) })
	mux.HandleFunc("/v1/admin/filter-count", func(w http.ResponseWriter, r *http.Request) { handleFilterCount(w, r, st) })
	mux.HandleFunc("/v1/pick", func(w http.ResponseWriter, r *http.Request) { handlePick(w, r, st) })
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		st.mu.RLock()
		defer st.mu.RUnlock()
		writeJSON(w, 200, healthResponse{OK: true, Service: "random-engine", IndexSize: len(st.byKey), SnapshotRevision: st.revision})
	})
	return mux
}

func seedSnapshot(t *testing.T, mux *http.ServeMux, st *engineState) {
	t.Helper()
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
	req := httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw))
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("snapshot status %d body %s", rr.Code, rr.Body.String())
	}
	if st.byKey == nil || len(st.byKey) != 3 {
		t.Fatalf("index size want 3 got %d", len(st.byKey))
	}
}

func TestRequireEngineSecret(t *testing.T) {
	st := &engineState{
		revision: "empty",
		byID:     map[int64]int{},
		tagIndex: map[string]map[int64]struct{}{},
	}
	inner := testMux(st)
	h := requireEngineSecret(inner, "sekrit")

	// healthz open
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rr.Code != 200 {
		t.Fatalf("healthz want 200 got %d", rr.Code)
	}

	// pick without secret → 403
	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(`{}`)))
	if rr.Code != http.StatusForbidden {
		t.Fatalf("pick no secret want 403 got %d", rr.Code)
	}

	// pick with wrong secret → 403
	req := httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(`{"filters":{},"strategy":"random","limit":1}`))
	req.Header.Set("X-Engine-Secret", "wrong")
	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("pick wrong secret want 403 got %d", rr.Code)
	}

	// pick with correct secret → reaches handler (INDEX_NOT_READY on empty)
	req = httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(`{"filters":{},"strategy":"random","limit":1}`))
	req.Header.Set("X-Engine-Secret", "sekrit")
	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("pick ok secret want 200 got %d body %s", rr.Code, rr.Body.String())
	}
}

func TestSnapshotAndRandomPick(t *testing.T) {
	st := &engineState{
		revision: "empty",
		byID:     map[int64]int{},
		tagIndex: map[string]map[int64]struct{}{},
	}
	mux := testMux(st)

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
	if empty.Code != "INDEX_NOT_READY" {
		t.Fatalf("want INDEX_NOT_READY got %s", empty.Code)
	}

	seedSnapshot(t, mux, st)

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

func TestFilterCountAndEvents(t *testing.T) {
	st := &engineState{
		revision: "empty",
		byID:     map[int64]int{},
		tagIndex: map[string]map[int64]struct{}{},
	}
	mux := testMux(st)
	seedSnapshot(t, mux, st)

	req := httptest.NewRequest(http.MethodPost, "/v1/admin/filter-count", bytes.NewBufferString(`{"filters":{"r18":0,"r18_strict":1}}`))
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("filter-count status %d body %s", rr.Code, rr.Body.String())
	}
	var fc map[string]any
	if err := json.Unmarshal(rr.Body.Bytes(), &fc); err != nil {
		t.Fatal(err)
	}
	if fc["ok"] != true {
		t.Fatalf("want ok true: %#v", fc)
	}
	// enabled safe only: ids 1 and 3
	if int(fc["filtered"].(float64)) != 2 {
		t.Fatalf("filtered want 2 got %#v", fc["filtered"])
	}
	if int(fc["index_size"].(float64)) != 3 {
		t.Fatalf("index_size want 3 got %#v", fc["index_size"])
	}

	// delete id=1 via events
	evBody := map[string]any{
		"events": []map[string]any{
			{"type": "image_deleted", "image_id": 1},
		},
	}
	raw, _ := json.Marshal(evBody)
	req = httptest.NewRequest(http.MethodPost, "/v1/admin/events", bytes.NewReader(raw))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("events status %d body %s", rr.Code, rr.Body.String())
	}
	if len(st.byKey) != 2 {
		t.Fatalf("after delete index want 2 got %d", len(st.byKey))
	}

	req = httptest.NewRequest(http.MethodPost, "/v1/admin/filter-count", bytes.NewBufferString(`{"filters":{"r18":0,"r18_strict":1}}`))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if err := json.Unmarshal(rr.Body.Bytes(), &fc); err != nil {
		t.Fatal(err)
	}
	if int(fc["filtered"].(float64)) != 1 {
		t.Fatalf("after delete filtered want 1 got %#v", fc["filtered"])
	}
}

func TestQualitySamplesCap(t *testing.T) {
	// Build many candidates so samples clamp is visible in debug.
	imgs := make([]map[string]any, 0, 80)
	for i := 1; i <= 80; i++ {
		imgs = append(imgs, map[string]any{
			"id": i, "illust_id": int64(1000 + i), "page_index": 0, "ext": "jpg",
			"status": 1, "random_key": float64(i) / 100.0, "x_restrict": 0,
			"bookmark_count": i, "view_count": i * 10, "tag_names": []string{},
		})
	}
	st := &engineState{
		revision: "empty",
		byID:     map[int64]int{},
		tagIndex: map[string]map[int64]struct{}{},
	}
	mux := testMux(st)
	raw, _ := json.Marshal(map[string]any{"revision": "big", "images": imgs})
	req := httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw))
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("snapshot %d %s", rr.Code, rr.Body.String())
	}

	// Request samples=999 → must clamp to 64 (Python QUALITY_SAMPLES_MAX).
	req = httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(
		`{"filters":{"r18":2},"strategy":"quality","quality":{"samples":999,"pick_mode":"best"},"limit":1,"debug":true}`,
	))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	var resp pickResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Code != "OK" || len(resp.Items) != 1 {
		t.Fatalf("pick failed: %#v", resp)
	}
	if resp.Items[0].ScoreDebug == nil {
		t.Fatalf("missing score_debug: %#v", resp.Items[0])
	}
	samples, ok := resp.Items[0].ScoreDebug["samples"].(float64)
	if !ok || int(samples) != 64 {
		t.Fatalf("samples want 64 got %#v", resp.Items[0].ScoreDebug["samples"])
	}
}

func TestClientDedupKeyShortWindow(t *testing.T) {
	// Two enabled images only so exclude of the first pick forces the second id.
	st := &engineState{
		revision:    "empty",
		byID:        map[int64]int{},
		tagIndex:    map[string]map[int64]struct{}{},
		clientDedup: newClientDedupStore(),
	}
	mux := testMux(st)
	body := map[string]any{
		"revision": "dedup",
		"images": []map[string]any{
			{"id": 11, "illust_id": 1100, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.1, "x_restrict": 0, "bookmark_count": 1, "view_count": 1, "tag_names": []string{}},
			{"id": 22, "illust_id": 2200, "page_index": 0, "ext": "png", "status": 1, "random_key": 0.9, "x_restrict": 0, "bookmark_count": 1, "view_count": 1, "tag_names": []string{}},
		},
	}
	raw, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw))
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("snapshot %d %s", rr.Code, rr.Body.String())
	}

	// Fixed seed keeps first pick stable; client_dedup_key records it for the next call.
	pickBody := `{"filters":{"r18":0,"r18_strict":1},"strategy":"random","limit":1,"seed":"fixed-dedup","client_dedup_key":"client-a"}`
	req = httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(pickBody))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	var first pickResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &first); err != nil {
		t.Fatal(err)
	}
	if first.Code != "OK" || len(first.Items) != 1 {
		t.Fatalf("first pick: %#v", first)
	}
	firstID := first.Items[0].ID

	req = httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(pickBody))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	var second pickResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &second); err != nil {
		t.Fatal(err)
	}
	if second.Code != "OK" || len(second.Items) != 1 {
		t.Fatalf("second pick: %#v", second)
	}
	if second.Items[0].ID == firstID {
		t.Fatalf("client_dedup_key did not exclude previous id %d", firstID)
	}

	// Different key must not inherit the first key's window.
	other := `{"filters":{"r18":0,"r18_strict":1},"strategy":"random","limit":1,"seed":"fixed-dedup","client_dedup_key":"client-b"}`
	req = httptest.NewRequest(http.MethodPost, "/v1/pick", bytes.NewBufferString(other))
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	var third pickResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &third); err != nil {
		t.Fatal(err)
	}
	if third.Code != "OK" || len(third.Items) != 1 {
		t.Fatalf("other-key pick: %#v", third)
	}
	if third.Items[0].ID != firstID {
		t.Fatalf("other key should still get first seed pick id=%d got=%d", firstID, third.Items[0].ID)
	}
}

func TestQualityBookmarkRatePerMille(t *testing.T) {
	// Python quality_score uses log1p((bm/vw)*1000); Go must match (not *100).
	bm := 10
	vw := 100
	im := indexImage{BookmarkCount: &bm, ViewCount: &vw}
	logit, _ := qualityLogit(im, map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 1, "freshness": 0, "bookmark_velocity": 0,
	}, map[string]float64{}, 21, 2)
	want := math.Log1p((10.0/100.0)*1000.0) // log1p(100)
	if math.Abs(logit-want) > 1e-9 {
		t.Fatalf("bookmark_rate logit want %v got %v", want, logit)
	}
}
