package main

import (
	"bytes"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
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
		writeJSON(w, 200, healthResponse{
			OK: true, Service: "random-engine", Ready: st.ready, IndexSize: len(st.byKey),
			SnapshotRevision: st.snapshotRevision, SnapshotManifestHash: st.snapshotManifestHash,
			CurrentStateHash: st.currentStateHash, StateVersion: st.stateVersion,
		})
	})
	return mux
}

func addSnapshotManifest(t *testing.T, st *engineState, body map[string]any) {
	t.Helper()
	raw, err := json.Marshal(body["images"])
	if err != nil {
		t.Fatal(err)
	}
	var images []indexImage
	if err := json.Unmarshal(raw, &images); err != nil {
		t.Fatal(err)
	}
	hash, err := snapshotContentHash(images, nil)
	if err != nil {
		t.Fatal(err)
	}
	body["complete"] = true
	body["expected_count"] = len(images)
	body["content_hash"] = hash
	body["base_state_version"] = st.stateVersion
}

func seedSnapshot(t *testing.T, mux *http.ServeMux, st *engineState) {
	t.Helper()
	body := map[string]any{
		"revision": "r1",
		"images": []map[string]any{
			{"id": 1, "illust_id": 100, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.1, "x_restrict": 0, "bookmark_count": 10, "view_count": 100, "tag_names": []string{"cat"}},
			{"id": 2, "illust_id": 200, "page_index": 0, "ext": "png", "status": 1, "random_key": 0.5, "x_restrict": 1, "bookmark_count": 50, "view_count": 200, "tag_names": []string{"dog"}},
			{"id": 3, "illust_id": 300, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.9, "x_restrict": 0, "bookmark_count": 5, "view_count": 50, "tag_names": []string{"cat", "cute"}},
		},
	}
	addSnapshotManifest(t, st, body)
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
	verifiedRevision := st.snapshotRevision
	verifiedManifestHash := st.snapshotManifestHash
	previousStateHash := st.currentStateHash

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
	if st.snapshotRevision != verifiedRevision || st.snapshotManifestHash != verifiedManifestHash {
		t.Fatal("event overwrote verified snapshot identity")
	}
	if st.currentStateHash == previousStateHash {
		t.Fatal("event did not advance current state hash")
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

func TestSnapshotManifestRejectsPartialAndEmptyStaysNotReady(t *testing.T) {
	st := &engineState{
		revision: "empty", currentStateHash: emptySnapshotContentHash(),
		byID: map[int64]int{}, tagIndex: map[string]map[int64]struct{}{},
	}
	mux := testMux(st)
	partial := map[string]any{
		"revision": "partial",
		"complete": false,
		"images": []map[string]any{
			{"id": 1, "illust_id": 10, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.1},
		},
	}
	addSnapshotManifest(t, st, partial)
	partial["complete"] = false
	raw, _ := json.Marshal(partial)
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw)))
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("partial snapshot want 400 got %d body=%s", rr.Code, rr.Body.String())
	}
	if st.ready || len(st.byKey) != 0 {
		t.Fatalf("partial snapshot changed readiness/index: ready=%v size=%d", st.ready, len(st.byKey))
	}
	eventRaw, _ := json.Marshal(map[string]any{"events": []map[string]any{{
		"type":  "image_upserted",
		"image": map[string]any{"id": 7, "illust_id": 70, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.7},
	}}})
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/v1/admin/events", bytes.NewReader(eventRaw)))
	if rr.Code != http.StatusOK || len(st.byKey) != 1 || st.ready {
		t.Fatalf("event-only index must remain not ready: code=%d ready=%v size=%d", rr.Code, st.ready, len(st.byKey))
	}

	empty := map[string]any{"revision": "empty-complete", "images": []map[string]any{}}
	addSnapshotManifest(t, st, empty)
	raw, _ = json.Marshal(empty)
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw)))
	if rr.Code != http.StatusOK {
		t.Fatalf("empty complete snapshot want 200 got %d body=%s", rr.Code, rr.Body.String())
	}
	if st.ready || !st.initialized || len(st.byKey) != 0 {
		t.Fatalf("empty snapshot must initialize but stay not ready: initialized=%v ready=%v size=%d", st.initialized, st.ready, len(st.byKey))
	}
}

func TestStaleSnapshotCannotOverwriteNewerEvent(t *testing.T) {
	st := &engineState{
		revision: "empty", currentStateHash: emptySnapshotContentHash(),
		byID: map[int64]int{}, tagIndex: map[string]map[int64]struct{}{},
	}
	mux := testMux(st)
	seedSnapshot(t, mux, st)
	verifiedRevision := st.snapshotRevision
	verifiedManifestHash := st.snapshotManifestHash

	stale := map[string]any{
		"revision": "stale-r2",
		"images": []map[string]any{
			{"id": 1, "illust_id": 100, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.1},
			{"id": 2, "illust_id": 200, "page_index": 0, "ext": "png", "status": 1, "random_key": 0.5},
			{"id": 3, "illust_id": 300, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.9},
		},
	}
	addSnapshotManifest(t, st, stale)

	evRaw, _ := json.Marshal(map[string]any{"events": []map[string]any{{"type": "image_deleted", "image_id": 1}}})
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/v1/admin/events", bytes.NewReader(evRaw)))
	if rr.Code != http.StatusOK {
		t.Fatalf("event want 200 got %d body=%s", rr.Code, rr.Body.String())
	}
	if _, exists := st.byID[1]; exists {
		t.Fatal("delete event did not remove image 1")
	}
	if st.snapshotRevision != verifiedRevision || st.snapshotManifestHash != verifiedManifestHash {
		t.Fatal("event overwrote verified snapshot identity")
	}

	raw, _ := json.Marshal(stale)
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw)))
	if rr.Code != http.StatusConflict {
		t.Fatalf("stale snapshot want 409 got %d body=%s", rr.Code, rr.Body.String())
	}
	if _, exists := st.byID[1]; exists {
		t.Fatal("stale snapshot resurrected deleted image 1")
	}
	if st.revision == "stale-r2" {
		t.Fatal("stale snapshot replaced revision")
	}
}

func TestSnapshotManifestValidatesRevisionCountAndHash(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(map[string]any)
	}{
		{name: "revision", mutate: func(body map[string]any) { body["revision"] = "" }},
		{name: "count", mutate: func(body map[string]any) { body["expected_count"] = 2 }},
		{name: "hash", mutate: func(body map[string]any) { body["content_hash"] = "deadbeef" }},
		{name: "illust_id", mutate: func(body map[string]any) { body["images"].([]map[string]any)[0]["illust_id"] = 0 }},
		{name: "page_index", mutate: func(body map[string]any) { body["images"].([]map[string]any)[0]["page_index"] = -1 }},
		{name: "ext", mutate: func(body map[string]any) { body["images"].([]map[string]any)[0]["ext"] = "exe" }},
		{name: "random_key", mutate: func(body map[string]any) { body["images"].([]map[string]any)[0]["random_key"] = 1.0 }},
		{name: "status", mutate: func(body map[string]any) { body["images"].([]map[string]any)[0]["status"] = 2 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			st := &engineState{
				revision: "empty", currentStateHash: emptySnapshotContentHash(),
				byID: map[int64]int{}, tagIndex: map[string]map[int64]struct{}{},
			}
			mux := testMux(st)
			body := map[string]any{
				"revision": "valid",
				"images": []map[string]any{
					{"id": 9, "illust_id": 90, "page_index": 0, "ext": "jpg", "status": 1, "random_key": 0.9},
				},
			}
			addSnapshotManifest(t, st, body)
			tc.mutate(body)
			raw, _ := json.Marshal(body)
			rr := httptest.NewRecorder()
			mux.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/v1/admin/snapshot", bytes.NewReader(raw)))
			if rr.Code != http.StatusBadRequest {
				t.Fatalf("invalid %s manifest want 400 got %d body=%s", tc.name, rr.Code, rr.Body.String())
			}
			if st.initialized || st.ready || st.stateVersion != 0 {
				t.Fatalf("invalid manifest changed state: initialized=%v ready=%v version=%d", st.initialized, st.ready, st.stateVersion)
			}
		})
	}
}

func TestSnapshotContentHashGolden(t *testing.T) {
	name := "作者"
	title := "<猫>"
	hash, err := snapshotContentHash([]indexImage{
		{ID: 2, IllustID: 20, PageIndex: 1, Ext: "png", Status: 1, RandomKey: 0.75, UserName: &name, Title: &title, TagNames: []string{"猫", "blue"}},
		{ID: 1, IllustID: 10, PageIndex: 0, Ext: "jpg", Status: 1, RandomKey: 0.25, TagNames: []string{}},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	const expected = "e247ea0d8ba8edffe3d4bd63e3f9475841e17c88b7bc8861e96fc668d41fd0a2"
	if hash != expected {
		t.Fatalf("hash want %s got %s", expected, hash)
	}
	valid := indexImage{ID: 1, IllustID: 10, PageIndex: 0, Ext: "jpg", Status: 1, RandomKey: 0.25}
	if _, err := snapshotContentHash([]indexImage{valid, valid}, nil); err == nil {
		t.Fatal("duplicate image ids must be rejected")
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
	body := map[string]any{"revision": "big", "images": imgs}
	addSnapshotManifest(t, st, body)
	raw, _ := json.Marshal(body)
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
	addSnapshotManifest(t, st, body)
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
	logit, _, _ := qualityLogit(im, map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 1, "freshness": 0, "bookmark_velocity": 0,
	}, map[string]float64{}, 21, 2, 1, nil, nil, 0, 0)
	want := math.Log1p((10.0 / 100.0) * 1000.0) // log1p(100)
	if math.Abs(logit-want) > 1e-9 {
		t.Fatalf("bookmark_rate logit want %v got %v", want, logit)
	}
}

func TestQualityTemperatureScalesScoreNotMultiplier(t *testing.T) {
	// Python: logit = score/T + log(mult). At T=2, mult=2 → score/2 + log(2).
	ai := 1 // AI multiplier path when multipliers["ai"]=2
	bm, vw := 10, 100
	im := indexImage{BookmarkCount: &bm, ViewCount: &vw, AIType: &ai}
	weights := map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 1, "freshness": 0, "bookmark_velocity": 0,
	}
	mults := map[string]float64{"ai": 2, "non_ai": 1, "unknown_ai": 1}
	logit, dbg, _ := qualityLogit(im, weights, mults, 21, 2, 2, nil, nil, 0, 0)
	score := math.Log1p(100.0) // only bookmark_rate term
	want := score/2.0 + math.Log(2.0)
	if math.Abs(logit-want) > 1e-9 {
		t.Fatalf("logit want %v got %v dbg=%v", want, logit, dbg)
	}
}

func TestQualityVelocityDenomFloor(t *testing.T) {
	// age≈0, smooth=0.1 → Python max(1.0, 0.1)=1.0; vel=bm/1.
	bm, vw := 10, 100
	created := time.Now().UTC().Format(time.RFC3339)
	im := indexImage{BookmarkCount: &bm, ViewCount: &vw, CreatedAtPixiv: &created}
	weights := map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 0, "freshness": 0, "bookmark_velocity": 1,
	}
	logit, dbg, _ := qualityLogit(im, weights, map[string]float64{}, 21, 0.1, 1, nil, nil, 0, 0)
	want := math.Log1p(10.0 / 1.0)
	if math.Abs(logit-want) > 1e-6 {
		t.Fatalf("velocity logit want %v got %v dbg=%v", want, logit, dbg)
	}
}

func TestQualitySoftDedupPenalties(t *testing.T) {
	// Python pick_by_quality: logit -= image/author penalty after base logit.
	bm, vw := 10, 100
	uid := int64(42)
	im := indexImage{ID: 7, BookmarkCount: &bm, ViewCount: &vw, UserID: &uid}
	weights := map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 1, "freshness": 0, "bookmark_velocity": 0,
	}
	base, _, ok := qualityLogit(im, weights, map[string]float64{}, 21, 2, 1, nil, nil, 0, 0)
	if !ok {
		t.Fatal("expected base logit ok")
	}
	recentImg := map[int64]struct{}{7: {}}
	recentAuth := map[int64]struct{}{42: {}}
	withImg, _, ok := qualityLogit(im, weights, map[string]float64{}, 21, 2, 1, recentImg, nil, 2.0, 0)
	if !ok || math.Abs((base-2.0)-withImg) > 1e-9 {
		t.Fatalf("image penalty: base %v penalized %v ok=%v", base, withImg, ok)
	}
	withAuth, _, ok := qualityLogit(im, weights, map[string]float64{}, 21, 2, 1, nil, recentAuth, 0, 1.5)
	if !ok || math.Abs((base-1.5)-withAuth) > 1e-9 {
		t.Fatalf("author penalty: base %v penalized %v ok=%v", base, withAuth, ok)
	}
	both, _, ok := qualityLogit(im, weights, map[string]float64{}, 21, 2, 1, recentImg, recentAuth, 2.0, 1.5)
	if !ok || math.Abs((base-3.5)-both) > 1e-9 {
		t.Fatalf("both penalties: base %v penalized %v ok=%v", base, both, ok)
	}
}

func TestQualityZeroMultiplierSkipped(t *testing.T) {
	// Python pick_by_quality continues when multiplier<=0; Go must not clamp to 1e-9.
	ai := 1
	bm, vw := 10, 100
	im := indexImage{BookmarkCount: &bm, ViewCount: &vw, AIType: &ai}
	weights := map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 1, "freshness": 0, "bookmark_velocity": 0,
	}
	mults := map[string]float64{"ai": 0, "non_ai": 1, "unknown_ai": 1}
	_, _, ok := qualityLogit(im, weights, mults, 21, 2, 1, nil, nil, 0, 0)
	if ok {
		t.Fatal("expected zero AI multiplier to skip candidate")
	}
}

func TestQualityFreshnessFallsBackToAddedAt(t *testing.T) {
	// Python score_image_with_time_boosts uses added_at when created_at_pixiv is missing.
	bm, vw := 0, 0
	// ~10 days old via added_at only
	added := time.Now().UTC().Add(-10 * 24 * time.Hour).Format(time.RFC3339)
	im := indexImage{BookmarkCount: &bm, ViewCount: &vw, AddedAt: &added}
	weights := map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 0, "freshness": 1, "bookmark_velocity": 0,
	}
	logit, dbg, ok := qualityLogit(im, weights, map[string]float64{}, 10, 2, 1, nil, nil, 0, 0)
	if !ok {
		t.Fatal("expected ok")
	}
	want := -10.0 / 10.0             // -age/half_life
	if math.Abs(logit-want) > 0.05 { // allow clock skew
		t.Fatalf("freshness from added_at want ~%v got %v dbg=%v", want, logit, dbg)
	}
	// Velocity must NOT use added_at fallback when created_at_pixiv is nil.
	weightsVel := map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 0,
		"bookmark_rate": 0, "freshness": 0, "bookmark_velocity": 1,
	}
	bm2 := 10
	im.BookmarkCount = &bm2
	logitVel, _, ok := qualityLogit(im, weightsVel, map[string]float64{}, 10, 2, 1, nil, nil, 0, 0)
	if !ok {
		t.Fatal("expected ok")
	}
	if math.Abs(logitVel) > 1e-9 {
		t.Fatalf("velocity should be 0 without created_at_pixiv, got %v", logitVel)
	}
}

func TestFilterByMultiplierAllow(t *testing.T) {
	ai, nonAI := 1, 0
	illust, manga := 0, 1
	imAI := indexImage{ID: 1, AIType: &ai, IllustType: &illust}
	imNon := indexImage{ID: 2, AIType: &nonAI, IllustType: &illust}
	imManga := indexImage{ID: 3, AIType: &nonAI, IllustType: &manga}
	mults := map[string]float64{"ai": 0, "non_ai": 1, "unknown_ai": 1, "illust": 1, "manga": 0, "ugoira": 1, "unknown_illust_type": 1}
	out := filterByMultiplierAllow([]indexImage{imAI, imNon, imManga}, mults)
	if len(out) != 1 || out[0].ID != 2 {
		t.Fatalf("want only non-AI illust id=2, got %+v", out)
	}
	// All multipliers default 1 → keep all.
	all := filterByMultiplierAllow([]indexImage{imAI, imNon, imManga}, map[string]float64{})
	if len(all) != 3 {
		t.Fatalf("default mults should keep all, got %d", len(all))
	}
}

func TestImageMultiplierMatchesQualityLogitGate(t *testing.T) {
	ai := 1
	bm, vw := 1, 1
	im := indexImage{AIType: &ai, BookmarkCount: &bm, ViewCount: &vw}
	mults := map[string]float64{"ai": 0}
	if imageMultiplier(im, mults) > 0 {
		t.Fatal("expected zero")
	}
	_, _, ok := qualityLogit(im, map[string]float64{"bookmark": 0, "view": 0, "comment": 0, "pixels": 0, "bookmark_rate": 0, "freshness": 0, "bookmark_velocity": 0}, mults, 21, 2, 1, nil, nil, 0, 0)
	if ok {
		t.Fatal("qualityLogit should skip zero mult")
	}
}

func TestImageMultiplierUnknownAIType(t *testing.T) {
	// Python: ai_type not in {0,1} → unknown_ai (not non_ai).
	other := 2
	im := indexImage{AIType: &other}
	mults := map[string]float64{"ai": 0.5, "non_ai": 0.0, "unknown_ai": 1.25}
	got := imageMultiplier(im, mults)
	if math.Abs(got-1.25) > 1e-9 {
		t.Fatalf("want unknown_ai 1.25, got %v", got)
	}
	zero := 0
	one := 1
	if math.Abs(imageMultiplier(indexImage{AIType: &zero}, mults)-0.0) > 1e-9 {
		t.Fatal("non_ai should apply 0")
	}
	if math.Abs(imageMultiplier(indexImage{AIType: &one}, mults)-0.5) > 1e-9 {
		t.Fatal("ai should apply 0.5")
	}
	if math.Abs(imageMultiplier(indexImage{}, mults)-1.25) > 1e-9 {
		t.Fatal("nil AIType should be unknown_ai")
	}
}

func TestFilterOrientationNoWidthHeightFallback(t *testing.T) {
	// Python: Image.orientation == N; null orientation never matches even if dims imply portrait.
	w, h := 100, 200
	ori := 1
	imNilOri := indexImage{ID: 1, Width: &w, Height: &h, RandomKey: 0.1}
	imOri := indexImage{ID: 2, Width: &w, Height: &h, Orientation: &ori, RandomKey: 0.2}
	st := &engineState{
		byKey:    []indexImage{imNilOri, imOri},
		byID:     map[int64]int{1: 0, 2: 1},
		tagIndex: map[string]map[int64]struct{}{},
	}
	out := filterImages(st, map[string]any{"orientation": "portrait", "r18": 2})
	if len(out) != 1 || out[0].ID != 2 {
		t.Fatalf("want only explicit orientation=1 id=2, got %+v", out)
	}
}

func TestFilterByMultiplierAllowSQLSetParity(t *testing.T) {
	// Python ai_type_allowed only adds NULL for unknown_ai — not ai_type=2.
	// Python illust_type_allowed only adds NULL for unknown_illust_type — not illust_type=9.
	// When allow set is complete ({0,1}+NULL / {0,1,2}+NULL), allowed_int_or_null_clause
	// returns no filter so out-of-range values still sample.
	ai1, ai0, aiOther := 1, 0, 2
	illust, otherIllust := 0, 9
	imNilAI := indexImage{ID: 1, IllustType: &illust} // AIType nil
	imAI1 := indexImage{ID: 2, AIType: &ai1, IllustType: &illust}
	imAI0 := indexImage{ID: 3, AIType: &ai0, IllustType: &illust}
	imAIOther := indexImage{ID: 4, AIType: &aiOther, IllustType: &illust}
	imIllustOther := indexImage{ID: 5, AIType: &ai0, IllustType: &otherIllust}
	imNilIllust := indexImage{ID: 6, AIType: &ai0} // IllustType nil
	all := []indexImage{imNilAI, imAI1, imAI0, imAIOther, imIllustOther, imNilIllust}

	// Only unknown_ai + illust: SQL would keep ai_type IS NULL AND illust_type=0.
	mults := map[string]float64{
		"ai": 0, "non_ai": 0, "unknown_ai": 1,
		"illust": 1, "manga": 0, "ugoira": 0, "unknown_illust_type": 0,
	}
	out := filterByMultiplierAllow(all, mults)
	if len(out) != 1 || out[0].ID != 1 {
		t.Fatalf("want only nil-AI illust id=1, got %+v", out)
	}
	// imageMultiplier still scores ai_type=2 as unknown_ai (scoring path, not sample allow-list).
	if imageMultiplier(imAIOther, mults) <= 0 {
		t.Fatal("scoring mult for ai_type=2 should still use unknown_ai")
	}

	// Only unknown_illust_type + non_ai: keep non_ai + illust_type IS NULL.
	mults2 := map[string]float64{
		"ai": 0, "non_ai": 1, "unknown_ai": 0,
		"illust": 0, "manga": 0, "ugoira": 0, "unknown_illust_type": 1,
	}
	out2 := filterByMultiplierAllow(all, mults2)
	if len(out2) != 1 || out2[0].ID != 6 {
		t.Fatalf("want only non_ai nil-illust id=6, got %+v", out2)
	}

	// Complete allow sets → open (no SQL clause): keep out-of-range too.
	openAll := filterByMultiplierAllow(all, map[string]float64{})
	if len(openAll) != 6 {
		t.Fatalf("complete allow sets should keep all including out-of-range, got %d", len(openAll))
	}

	// All zero AI mults → empty (Python early return).
	empty := filterByMultiplierAllow([]indexImage{imNilAI, imAI1}, map[string]float64{"ai": 0, "non_ai": 0, "unknown_ai": 0})
	if len(empty) != 0 {
		t.Fatalf("empty AI allow-set should drop all, got %d", len(empty))
	}
}

func TestQualityScoreNonnegCountersAndPixels(t *testing.T) {
	// Python as_nonneg_int: negative counters → 0; pixels only when width>0 && height>0.
	neg, zero, posW, posH := -5, 0, 100, 200
	im := indexImage{
		BookmarkCount: &neg,
		ViewCount:     &posW, // positive so rate path not the only concern
		CommentCount:  &neg,
		Width:         &neg,
		Height:        &posH,
	}
	weights := map[string]float64{
		"bookmark": 1, "view": 0, "comment": 1, "pixels": 1,
		"bookmark_rate": 0, "freshness": 0, "bookmark_velocity": 0,
	}
	logit, _, ok := qualityLogit(im, weights, map[string]float64{}, 21, 2, 1, nil, nil, 0, 0)
	if !ok {
		t.Fatal("expected ok")
	}
	// bm=0, cm=0, pixels=0 → score 0
	if math.Abs(logit) > 1e-9 {
		t.Fatalf("negative counters/dims should score 0, got %v", logit)
	}

	im.Width = &posW
	im.Height = &zero
	logit2, _, _ := qualityLogit(im, weights, map[string]float64{}, 21, 2, 1, nil, nil, 0, 0)
	if math.Abs(logit2) > 1e-9 {
		t.Fatalf("height<=0 should yield pixels=0, got %v", logit2)
	}

	im.Height = &posH
	bm := 10
	im.BookmarkCount = &bm
	im.ViewCount = &posW
	im.CommentCount = &zero
	weightsPx := map[string]float64{
		"bookmark": 0, "view": 0, "comment": 0, "pixels": 1,
		"bookmark_rate": 0, "freshness": 0, "bookmark_velocity": 0,
	}
	logit3, _, _ := qualityLogit(im, weightsPx, map[string]float64{}, 21, 2, 1, nil, nil, 0, 0)
	want := math.Log1p(float64(posW*posH) / 1_000_000.0)
	if math.Abs(logit3-want) > 1e-9 {
		t.Fatalf("pixels want %v got %v", want, logit3)
	}
}
