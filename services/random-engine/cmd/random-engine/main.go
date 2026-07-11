package main

import (
	"encoding/json"
	"hash/fnv"
	"log"
	"math"
	"math/rand"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Random Engine — in-memory filter + random/quality pick.
// Contract: contracts/random-engine.openapi.yaml
// No image I/O, no residential proxies. BFF adapts public GET /random.

type pickRequest struct {
	Filters        map[string]any `json:"filters"`
	Strategy       string         `json:"strategy"`
	Quality        map[string]any `json:"quality"`
	Seed           *string        `json:"seed"`
	Limit          int            `json:"limit"`
	Debug          bool           `json:"debug"`
	ClientDedupKey *string        `json:"client_dedup_key"`
}

type indexImage struct {
	ID             int64    `json:"id"`
	IllustID       int64    `json:"illust_id"`
	PageIndex      int      `json:"page_index"`
	Ext            string   `json:"ext"`
	Status         any      `json:"status"` // DB int 1..4; only 1 is enabled for pick
	RandomKey      float64  `json:"random_key"`
	Width          *int     `json:"width"`
	Height         *int     `json:"height"`
	Orientation    *int     `json:"orientation"` // 1 portrait 2 landscape 3 square
	XRestrict      *int     `json:"x_restrict"`
	AIType         *int     `json:"ai_type"`
	IllustType     *int     `json:"illust_type"`
	UserID         *int64   `json:"user_id"`
	UserName       *string  `json:"user_name"`
	Title          *string  `json:"title"`
	CreatedAtPixiv *string  `json:"created_at_pixiv"`
	BookmarkCount  *int     `json:"bookmark_count"`
	ViewCount      *int     `json:"view_count"`
	CommentCount   *int     `json:"comment_count"`
	OriginalURL    *string  `json:"original_url"`
	TagIDs         []int64  `json:"tag_ids"`
	TagNames       []string `json:"tag_names"`
	LastFailAt     *string  `json:"last_fail_at"`
	LastErrorCode  *string  `json:"last_error_code"`
}

type pickItem struct {
	ID             int64          `json:"id"`
	IllustID       int64          `json:"illust_id"`
	PageIndex      int            `json:"page_index"`
	Ext            string         `json:"ext"`
	Width          *int           `json:"width,omitempty"`
	Height         *int           `json:"height,omitempty"`
	XRestrict      *int           `json:"x_restrict,omitempty"`
	AIType         *int           `json:"ai_type,omitempty"`
	IllustType     *int           `json:"illust_type,omitempty"`
	UserID         *int64         `json:"user_id,omitempty"`
	UserName       *string        `json:"user_name,omitempty"`
	Title          *string        `json:"title,omitempty"`
	CreatedAtPixiv *string        `json:"created_at_pixiv,omitempty"`
	BookmarkCount  *int           `json:"bookmark_count,omitempty"`
	ViewCount      *int           `json:"view_count,omitempty"`
	CommentCount   *int           `json:"comment_count,omitempty"`
	OriginalURL    *string        `json:"original_url,omitempty"`
	EdgePath       string         `json:"edge_path"`
	ScoreDebug     map[string]any `json:"score_debug,omitempty"`
}

type pickResponse struct {
	OK    bool       `json:"ok"`
	Code  string     `json:"code"`
	Items []pickItem `json:"items"`
	Debug any        `json:"debug,omitempty"`
}

type healthResponse struct {
	OK               bool   `json:"ok"`
	Service          string `json:"service"`
	IndexSize        int    `json:"index_size"`
	SnapshotRevision string `json:"snapshot_revision"`
}

// Short-window per-client anti-repeat (process-local). Complements filters.exclude_image_ids.
// Fail-open: missing/empty key is a no-op. Multi-instance engines do not share this map.
const (
	clientDedupMaxIDs = 64
	clientDedupTTL    = 10 * time.Minute
)

type clientDedupWindow struct {
	ids  []int64
	last time.Time
}

type clientDedupStore struct {
	mu      sync.Mutex
	windows map[string]*clientDedupWindow
}

func newClientDedupStore() *clientDedupStore {
	return &clientDedupStore{windows: map[string]*clientDedupWindow{}}
}

func (s *clientDedupStore) recentIDs(key string) []int64 {
	key = strings.TrimSpace(key)
	if key == "" || s == nil {
		return nil
	}
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	w, ok := s.windows[key]
	if !ok || w == nil {
		return nil
	}
	if now.Sub(w.last) > clientDedupTTL {
		delete(s.windows, key)
		return nil
	}
	out := make([]int64, len(w.ids))
	copy(out, w.ids)
	return out
}

func (s *clientDedupStore) record(key string, ids []int64) {
	key = strings.TrimSpace(key)
	if key == "" || s == nil || len(ids) == 0 {
		return
	}
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	// Opportunistic prune of stale keys (bounded map growth).
	if len(s.windows) > 10_000 {
		for k, w := range s.windows {
			if w == nil || now.Sub(w.last) > clientDedupTTL {
				delete(s.windows, k)
			}
		}
	}
	w := s.windows[key]
	if w == nil {
		w = &clientDedupWindow{}
		s.windows[key] = w
	}
	for _, id := range ids {
		if id == 0 {
			continue
		}
		// de-dupe while appending
		exists := false
		for _, prev := range w.ids {
			if prev == id {
				exists = true
				break
			}
		}
		if !exists {
			w.ids = append(w.ids, id)
		}
	}
	if len(w.ids) > clientDedupMaxIDs {
		w.ids = append([]int64(nil), w.ids[len(w.ids)-clientDedupMaxIDs:]...)
	}
	w.last = now
}

type engineState struct {
	mu       sync.RWMutex
	revision string
	// sorted by random_key ascending for ring sampling
	byKey []indexImage
	// id -> index in byKey (rebuilt on snapshot)
	byID map[int64]int
	// exact tag name -> set of image ids (matches SQL Tag.name)
	tagIndex map[string]map[int64]struct{}
	// optional per-client short-window excludes (client_dedup_key)
	clientDedup *clientDedupStore
}

func main() {
	addr := envOr("RANDOM_ENGINE_ADDR", ":8091")
	st := &engineState{
		revision:    "empty",
		byID:        map[int64]int{},
		tagIndex:    map[string]map[int64]struct{}{},
		clientDedup: newClientDedupStore(),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		st.mu.RLock()
		defer st.mu.RUnlock()
		writeJSON(w, http.StatusOK, healthResponse{
			OK:               true,
			Service:          "random-engine",
			IndexSize:        len(st.byKey),
			SnapshotRevision: st.revision,
		})
	})
	mux.HandleFunc("/v1/pick", func(w http.ResponseWriter, r *http.Request) {
		handlePick(w, r, st)
	})
	mux.HandleFunc("/v1/feed", func(w http.ResponseWriter, r *http.Request) {
		handlePick(w, r, st)
	})
	mux.HandleFunc("/v1/admin/snapshot", func(w http.ResponseWriter, r *http.Request) {
		handleSnapshot(w, r, st)
	})
	mux.HandleFunc("/v1/admin/events", func(w http.ResponseWriter, r *http.Request) {
		handleEvents(w, r, st)
	})
	mux.HandleFunc("/v1/admin/filter-count", func(w http.ResponseWriter, r *http.Request) {
		handleFilterCount(w, r, st)
	})

	log.Printf("random-engine listening on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}

func handleSnapshot(w http.ResponseWriter, r *http.Request, st *engineState) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Revision    string               `json:"revision"`
		Images      []indexImage         `json:"images"`
		TagPostings map[string][]int64   `json:"tag_postings"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}

	// Keep only enabled images (status == 1), matching Python Image.status == 1.
	imgs := make([]indexImage, 0, len(body.Images))
	for _, im := range body.Images {
		if !isEnabledStatus(im.Status) {
			continue
		}
		if im.RandomKey < 0 {
			im.RandomKey = 0
		}
		if im.RandomKey >= 1 {
			im.RandomKey = math.Mod(im.RandomKey, 1.0)
		}
		imgs = append(imgs, im)
	}
	sort.Slice(imgs, func(i, j int) bool {
		if imgs[i].RandomKey == imgs[j].RandomKey {
			return imgs[i].ID < imgs[j].ID
		}
		return imgs[i].RandomKey < imgs[j].RandomKey
	})

	byID := make(map[int64]int, len(imgs))
	tagIndex := map[string]map[int64]struct{}{}
	for i, im := range imgs {
		byID[im.ID] = i
		for _, tn := range im.TagNames {
			k := strings.TrimSpace(tn)
			if k == "" {
				continue
			}
			if tagIndex[k] == nil {
				tagIndex[k] = map[int64]struct{}{}
			}
			tagIndex[k][im.ID] = struct{}{}
		}
	}
	for name, ids := range body.TagPostings {
		k := strings.TrimSpace(name)
		if k == "" {
			continue
		}
		if tagIndex[k] == nil {
			tagIndex[k] = map[int64]struct{}{}
		}
		for _, id := range ids {
			if _, ok := byID[id]; ok {
				tagIndex[k][id] = struct{}{}
			}
		}
	}

	rev := body.Revision
	if rev == "" {
		rev = time.Now().UTC().Format(time.RFC3339Nano)
	}

	st.mu.Lock()
	st.byKey = imgs
	st.byID = byID
	st.tagIndex = tagIndex
	st.revision = rev
	size := len(imgs)
	st.mu.Unlock()

	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "index_size": size, "revision": rev})
}

func handleEvents(w http.ResponseWriter, r *http.Request, st *engineState) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Events []struct {
			Type    string      `json:"type"`
			Image   *indexImage `json:"image"`
			ImageID *int64      `json:"image_id"`
			Status  any         `json:"status"`
		} `json:"events"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}

	st.mu.Lock()
	defer st.mu.Unlock()

	m := make(map[int64]indexImage, len(st.byKey))
	for _, im := range st.byKey {
		m[im.ID] = im
	}
	applied := 0
	for _, ev := range body.Events {
		switch ev.Type {
		case "image_upserted":
			if ev.Image == nil {
				continue
			}
			im := *ev.Image
			if !isEnabledStatus(im.Status) {
				delete(m, im.ID)
			} else {
				m[im.ID] = im
			}
			applied++
		case "image_deleted":
			id := int64(0)
			if ev.ImageID != nil {
				id = *ev.ImageID
			} else if ev.Image != nil {
				id = ev.Image.ID
			}
			if id != 0 {
				delete(m, id)
				applied++
			}
		case "image_status_changed":
			id := int64(0)
			if ev.ImageID != nil {
				id = *ev.ImageID
			} else if ev.Image != nil {
				id = ev.Image.ID
			}
			if id == 0 {
				continue
			}
			im, ok := m[id]
			if !ok && ev.Image != nil {
				im = *ev.Image
			}
			if ev.Status != nil {
				im.Status = ev.Status
			}
			if isEnabledStatus(im.Status) {
				m[id] = im
			} else {
				delete(m, id)
			}
			applied++
		case "tags_replaced":
			if ev.Image == nil {
				continue
			}
			im, ok := m[ev.Image.ID]
			if !ok {
				im = *ev.Image
			}
			im.TagIDs = ev.Image.TagIDs
			im.TagNames = ev.Image.TagNames
			if isEnabledStatus(im.Status) {
				m[im.ID] = im
			}
			applied++
		}
	}

	imgs := make([]indexImage, 0, len(m))
	for _, im := range m {
		imgs = append(imgs, im)
	}
	sort.Slice(imgs, func(i, j int) bool {
		if imgs[i].RandomKey == imgs[j].RandomKey {
			return imgs[i].ID < imgs[j].ID
		}
		return imgs[i].RandomKey < imgs[j].RandomKey
	})
	byID := make(map[int64]int, len(imgs))
	tagIndex := map[string]map[int64]struct{}{}
	for i, im := range imgs {
		byID[im.ID] = i
		for _, tn := range im.TagNames {
			k := strings.TrimSpace(tn)
			if k == "" {
				continue
			}
			if tagIndex[k] == nil {
				tagIndex[k] = map[int64]struct{}{}
			}
			tagIndex[k][im.ID] = struct{}{}
		}
	}
	st.byKey = imgs
	st.byID = byID
	st.tagIndex = tagIndex
	st.revision = time.Now().UTC().Format(time.RFC3339Nano)

	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "applied": applied, "index_size": len(imgs), "revision": st.revision})
}

func handleFilterCount(w http.ResponseWriter, r *http.Request, st *engineState) {
	// Dual-run ops: cardinality of filtered index without sampling noise.
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Filters map[string]any `json:"filters"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	st.mu.RLock()
	defer st.mu.RUnlock()
	filtered := filterImages(st, body.Filters)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":         true,
		"filtered":   len(filtered),
		"index_size": len(st.byKey),
		"revision":   st.revision,
	})
}

func handlePick(w http.ResponseWriter, r *http.Request, st *engineState) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req pickRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	if req.Limit < 1 {
		req.Limit = 1
	}
	if req.Limit > 100 {
		req.Limit = 100
	}
	if req.Strategy == "" {
		req.Strategy = "random"
	}
	if req.Strategy != "random" && req.Strategy != "quality" {
		http.Error(w, "unsupported strategy", http.StatusBadRequest)
		return
	}

	st.mu.RLock()
	defer st.mu.RUnlock()

	if len(st.byKey) == 0 {
		resp := pickResponse{OK: true, Code: "NO_MATCH", Items: []pickItem{}}
		if req.Debug {
			resp.Debug = map[string]any{"reason": "empty_index", "revision": st.revision}
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}

	rng := rngFromSeed(req.Seed)
	filters := req.Filters
	dedupKey := ""
	if req.ClientDedupKey != nil {
		dedupKey = strings.TrimSpace(*req.ClientDedupKey)
	}
	if dedupKey != "" && st.clientDedup != nil {
		if recent := st.clientDedup.recentIDs(dedupKey); len(recent) > 0 {
			filters = mergeExcludeImageIDs(filters, recent)
		}
	}
	candidates := filterImages(st, filters)
	if len(candidates) == 0 {
		resp := pickResponse{OK: true, Code: "NO_MATCH", Items: []pickItem{}}
		if req.Debug {
			resp.Debug = map[string]any{"reason": "filtered_empty", "revision": st.revision, "index_size": len(st.byKey)}
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}

	var picked []indexImage
	var scoreDebug map[string]any
	if req.Strategy == "quality" {
		samples := intFromAny(req.Quality["samples"], 12)
		if samples < 1 {
			samples = 1
		}
		// Align Python QUALITY_SAMPLES_MAX_QUERY/AUTO (=64).
		if samples > 64 {
			samples = 64
		}
		if samples > len(candidates) {
			samples = len(candidates)
		}
		pool := ringSample(candidates, samples, rng)
		pickMode := stringFromAny(req.Quality["pick_mode"], "weighted")
		temp := floatFromAny(req.Quality["temperature"], 1.0)
		weights := mapFromAny(req.Quality["weights"])
		multipliers := mapFromAny(req.Quality["multipliers"])
		halfLife := floatFromAny(req.Quality["freshness_half_life_days"], 21.0)
		velSmooth := floatFromAny(req.Quality["velocity_smooth_days"], 2.0)

		scored := make([]scoredImg, 0, len(pool))
		for _, im := range pool {
			logit, dbg := qualityLogit(im, weights, multipliers, halfLife, velSmooth)
			scored = append(scored, scoredImg{im: im, logit: logit, dbg: dbg})
		}
		chosen := pickFromScored(scored, pickMode, temp, req.Limit, rng)
		picked = make([]indexImage, 0, len(chosen))
		for _, c := range chosen {
			picked = append(picked, c.im)
		}
		if req.Debug && len(chosen) > 0 {
			scoreDebug = map[string]any{
				"strategy":  "quality",
				"samples":   samples,
				"pool":      len(pool),
				"pick_mode": pickMode,
				"top":       chosen[0].dbg,
			}
		}
	} else {
		picked = ringSample(candidates, req.Limit, rng)
	}

	items := make([]pickItem, 0, len(picked))
	for _, im := range picked {
		item := toPickItem(im)
		if req.Debug && scoreDebug != nil && len(items) == 0 {
			item.ScoreDebug = scoreDebug
		}
		items = append(items, item)
	}

	if dedupKey != "" && st.clientDedup != nil && len(items) > 0 {
		ids := make([]int64, 0, len(items))
		for _, it := range items {
			ids = append(ids, it.ID)
		}
		st.clientDedup.record(dedupKey, ids)
	}

	resp := pickResponse{OK: true, Code: "OK", Items: items}
	if req.Debug {
		resp.Debug = map[string]any{
			"revision":   st.revision,
			"index_size": len(st.byKey),
			"filtered":   len(candidates),
			"strategy":   req.Strategy,
			"returned":   len(items),
		}
	}
	if len(items) == 0 {
		resp.Code = "NO_MATCH"
	}
	writeJSON(w, http.StatusOK, resp)
}

// mergeExcludeImageIDs returns a shallow copy of filters with extra ids unioned into exclude_image_ids.
func mergeExcludeImageIDs(filters map[string]any, extra []int64) map[string]any {
	if len(extra) == 0 {
		return filters
	}
	out := map[string]any{}
	if filters != nil {
		for k, v := range filters {
			out[k] = v
		}
	}
	seen := map[int64]struct{}{}
	merged := make([]any, 0, len(extra)+8)
	if raw, ok := out["exclude_image_ids"]; ok {
		switch v := raw.(type) {
		case []any:
			for _, x := range v {
				id := int64FromAny(x, 0)
				if id == 0 {
					continue
				}
				if _, e := seen[id]; e {
					continue
				}
				seen[id] = struct{}{}
				merged = append(merged, id)
			}
		case []float64:
			for _, x := range v {
				id := int64(x)
				if id == 0 {
					continue
				}
				if _, e := seen[id]; e {
					continue
				}
				seen[id] = struct{}{}
				merged = append(merged, id)
			}
		}
	}
	for _, id := range extra {
		if id == 0 {
			continue
		}
		if _, e := seen[id]; e {
			continue
		}
		seen[id] = struct{}{}
		merged = append(merged, id)
	}
	out["exclude_image_ids"] = merged
	return out
}

type scoredImg struct {
	im    indexImage
	logit float64
	dbg   map[string]any
}

func pickFromScored(scored []scoredImg, pickMode string, temperature float64, limit int, rng *rand.Rand) []scoredImg {
	if len(scored) == 0 {
		return nil
	}
	if limit < 1 {
		limit = 1
	}
	if limit > len(scored) {
		limit = len(scored)
	}
	if strings.EqualFold(pickMode, "best") {
		sort.Slice(scored, func(i, j int) bool { return scored[i].logit > scored[j].logit })
		return scored[:limit]
	}
	if temperature <= 0 {
		temperature = 1
	}
	out := make([]scoredImg, 0, limit)
	remain := append([]scoredImg(nil), scored...)
	for len(out) < limit && len(remain) > 0 {
		maxL := remain[0].logit
		for _, s := range remain {
			if s.logit > maxL {
				maxL = s.logit
			}
		}
		weights := make([]float64, len(remain))
		sum := 0.0
		for i, s := range remain {
			w := math.Exp((s.logit - maxL) / temperature)
			weights[i] = w
			sum += w
		}
		if sum <= 0 {
			idx := rng.Intn(len(remain))
			out = append(out, remain[idx])
			remain = append(remain[:idx], remain[idx+1:]...)
			continue
		}
		x := rng.Float64() * sum
		acc := 0.0
		idx := len(remain) - 1
		for i, w := range weights {
			acc += w
			if x <= acc {
				idx = i
				break
			}
		}
		out = append(out, remain[idx])
		remain = append(remain[:idx], remain[idx+1:]...)
	}
	return out
}

func qualityLogit(im indexImage, weights, multipliers map[string]float64, halfLifeDays, velSmooth float64) (float64, map[string]any) {
	wBookmark := weightOr(weights, "bookmark", 4.0)
	wView := weightOr(weights, "view", 0.5)
	wComment := weightOr(weights, "comment", 2.0)
	wPixels := weightOr(weights, "pixels", 1.0)
	wRate := weightOr(weights, "bookmark_rate", 3.0)
	wFresh := weightOr(weights, "freshness", 1.0)
	wVel := weightOr(weights, "bookmark_velocity", 1.2)

	bm := float64(intOr(im.BookmarkCount, 0))
	vw := float64(intOr(im.ViewCount, 0))
	cm := float64(intOr(im.CommentCount, 0))
	pixels := 0.0
	if im.Width != nil && im.Height != nil {
		pixels = float64(*im.Width) * float64(*im.Height)
	}
	rate := 0.0
	if vw > 0 {
		rate = bm / vw
	}

	age := ageDaysFromPixiv(im.CreatedAtPixiv)
	fresh := 0.0
	if age != nil && halfLifeDays > 0 {
		fresh = math.Exp(-(*age) * math.Ln2 / halfLifeDays)
	}
	vel := 0.0
	if age != nil {
		den := *age + math.Max(0.1, velSmooth)
		vel = bm / den
	}

	logit := wBookmark*math.Log1p(bm) +
		wView*math.Log1p(vw) +
		wComment*math.Log1p(cm) +
		wPixels*math.Log1p(pixels/1_000_000.0) +
		wRate*math.Log1p(rate*100.0) +
		wFresh*fresh +
		wVel*math.Log1p(vel)

	mult := 1.0
	if im.AIType == nil {
		mult *= weightOr(multipliers, "unknown_ai", 1.0)
	} else if *im.AIType == 1 {
		mult *= weightOr(multipliers, "ai", 1.0)
	} else {
		mult *= weightOr(multipliers, "non_ai", 1.0)
	}
	if im.IllustType == nil {
		mult *= weightOr(multipliers, "unknown_illust_type", 1.0)
	} else {
		switch *im.IllustType {
		case 0:
			mult *= weightOr(multipliers, "illust", 1.0)
		case 1:
			mult *= weightOr(multipliers, "manga", 1.0)
		case 2:
			mult *= weightOr(multipliers, "ugoira", 1.0)
		default:
			mult *= weightOr(multipliers, "unknown_illust_type", 1.0)
		}
	}
	if mult <= 0 {
		mult = 1e-9
	}
	logit += math.Log(mult)

	dbg := map[string]any{
		"logit":      logit,
		"bookmark":   bm,
		"view":       vw,
		"freshness":  fresh,
		"velocity":   vel,
		"multiplier": mult,
	}
	return logit, dbg
}

func filterImages(st *engineState, filters map[string]any) []indexImage {
	if filters == nil {
		filters = map[string]any{}
	}
	r18 := intFromAny(filters["r18"], 0)
	r18Strict := intFromAny(filters["r18_strict"], 0) == 1
	aiType := strings.ToLower(stringFromAny(filters["ai_type"], "any"))
	illustTypeRaw := stringFromAny(filters["illust_type"], "")
	orientation := strings.ToLower(stringFromAny(filters["orientation"], "any"))
	minW := intFromAny(filters["min_width"], 0)
	minH := intFromAny(filters["min_height"], 0)
	minPx := intFromAny(filters["min_pixels"], 0)
	minBm := intFromAny(filters["min_bookmarks"], 0)
	minVw := intFromAny(filters["min_views"], 0)
	minCm := intFromAny(filters["min_comments"], 0)
	userID := int64FromAny(filters["user_id"], 0)
	illustID := int64FromAny(filters["illust_id"], 0)
	createdFrom := stringFromAny(filters["created_from"], "")
	createdTo := stringFromAny(filters["created_to"], "")
	failCooldown := stringFromAny(filters["fail_cooldown_before"], "")

	excludeIDs := map[int64]struct{}{}
	if raw, ok := filters["exclude_image_ids"]; ok {
		switch v := raw.(type) {
		case []any:
			for _, x := range v {
				id := int64FromAny(x, 0)
				if id != 0 {
					excludeIDs[id] = struct{}{}
				}
			}
		case []float64:
			for _, x := range v {
				excludeIDs[int64(x)] = struct{}{}
			}
		}
	}

	incGroups := parseTagGroups(filters["included_tags"])
	excNames := flattenTagNames(filters["excluded_tags"])

	out := make([]indexImage, 0, len(st.byKey)/4+1)
	for _, im := range st.byKey {
		if _, ex := excludeIDs[im.ID]; ex {
			continue
		}
		if illustID != 0 && im.IllustID != illustID {
			continue
		}
		if userID != 0 {
			if im.UserID == nil || *im.UserID != userID {
				continue
			}
		}
		// r18: align Python _r18_where_clause
		// 0 safe, 1 r18 only, 2 any
		xr := 0
		hasXR := false
		if im.XRestrict != nil {
			xr = *im.XRestrict
			hasXR = true
		}
		switch r18 {
		case 1:
			if !hasXR || xr != 1 {
				continue
			}
		case 2:
			// any
		default:
			if r18Strict {
				if !hasXR || xr != 0 {
					continue
				}
			} else {
				if hasXR && xr != 0 {
					continue
				}
			}
		}
		if aiType == "0" || aiType == "1" {
			want, _ := strconv.Atoi(aiType)
			if im.AIType == nil || *im.AIType != want {
				continue
			}
		}
		if illustTypeRaw != "" && !strings.EqualFold(illustTypeRaw, "any") {
			want, err := strconv.Atoi(illustTypeRaw)
			if err == nil {
				if im.IllustType == nil || *im.IllustType != want {
					continue
				}
			}
		}
		if orientation != "" && orientation != "any" {
			wantOri := 0
			switch orientation {
			case "portrait":
				wantOri = 1
			case "landscape":
				wantOri = 2
			case "square":
				wantOri = 3
			}
			if wantOri != 0 {
				if im.Orientation != nil {
					if *im.Orientation != wantOri {
						continue
					}
				} else if im.Width != nil && im.Height != nil {
					w, h := *im.Width, *im.Height
					ok := false
					switch wantOri {
					case 1:
						ok = h > w
					case 2:
						ok = w > h
					case 3:
						ok = w == h
					}
					if !ok {
						continue
					}
				} else {
					continue
				}
			}
		}
		if minW > 0 && (im.Width == nil || *im.Width < minW) {
			continue
		}
		if minH > 0 && (im.Height == nil || *im.Height < minH) {
			continue
		}
		if minPx > 0 {
			if im.Width == nil || im.Height == nil || (*im.Width)*(*im.Height) < minPx {
				continue
			}
		}
		if minBm > 0 && intOr(im.BookmarkCount, 0) < minBm {
			continue
		}
		if minVw > 0 && intOr(im.ViewCount, 0) < minVw {
			continue
		}
		if minCm > 0 && intOr(im.CommentCount, 0) < minCm {
			continue
		}
		if createdFrom != "" || createdTo != "" {
			ca := ""
			if im.CreatedAtPixiv != nil {
				ca = *im.CreatedAtPixiv
			}
			if createdFrom != "" && ca < createdFrom {
				continue
			}
			if createdTo != "" && ca > createdTo {
				continue
			}
		}
		if failCooldown != "" {
			// Python: last_fail_at IS NULL OR last_fail_at <= fail_cooldown_before
			if im.LastFailAt != nil && *im.LastFailAt > failCooldown {
				continue
			}
		}
		if len(incGroups) > 0 {
			okAll := true
			for _, group := range incGroups {
				okGroup := false
				for _, name := range group {
					if ids, ok := st.tagIndex[name]; ok {
						if _, hit := ids[im.ID]; hit {
							okGroup = true
							break
						}
					}
					for _, tn := range im.TagNames {
						if strings.TrimSpace(tn) == name {
							okGroup = true
							break
						}
					}
					if okGroup {
						break
					}
				}
				if !okGroup {
					okAll = false
					break
				}
			}
			if !okAll {
				continue
			}
		}
		if len(excNames) > 0 {
			hit := false
			for _, name := range excNames {
				if ids, ok := st.tagIndex[name]; ok {
					if _, ok2 := ids[im.ID]; ok2 {
						hit = true
						break
					}
				}
				for _, tn := range im.TagNames {
					if strings.TrimSpace(tn) == name {
						hit = true
						break
					}
				}
				if hit {
					break
				}
			}
			if hit {
				continue
			}
		}
		out = append(out, im)
	}
	return out
}

func ringSample(candidates []indexImage, n int, rng *rand.Rand) []indexImage {
	if n <= 0 || len(candidates) == 0 {
		return nil
	}
	if n >= len(candidates) {
		cp := append([]indexImage(nil), candidates...)
		rng.Shuffle(len(cp), func(i, j int) { cp[i], cp[j] = cp[j], cp[i] })
		return cp
	}
	startKey := rng.Float64()
	i := sort.Search(len(candidates), func(i int) bool {
		return candidates[i].RandomKey >= startKey
	})
	if i >= len(candidates) {
		i = 0
	}
	out := make([]indexImage, 0, n)
	seen := map[int64]struct{}{}
	for len(out) < n {
		im := candidates[i%len(candidates)]
		if _, ok := seen[im.ID]; !ok {
			seen[im.ID] = struct{}{}
			out = append(out, im)
		}
		i++
		if len(seen) >= len(candidates) {
			break
		}
	}
	return out
}

func toPickItem(im indexImage) pickItem {
	return pickItem{
		ID:             im.ID,
		IllustID:       im.IllustID,
		PageIndex:      im.PageIndex,
		Ext:            im.Ext,
		Width:          im.Width,
		Height:         im.Height,
		XRestrict:      im.XRestrict,
		AIType:         im.AIType,
		IllustType:     im.IllustType,
		UserID:         im.UserID,
		UserName:       im.UserName,
		Title:          im.Title,
		CreatedAtPixiv: im.CreatedAtPixiv,
		BookmarkCount:  im.BookmarkCount,
		ViewCount:      im.ViewCount,
		CommentCount:   im.CommentCount,
		OriginalURL:    im.OriginalURL,
		EdgePath:       "/i/" + strconv.FormatInt(im.ID, 10) + "." + im.Ext,
	}
}

func isEnabledStatus(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case int:
		return x == 1
	case int64:
		return x == 1
	case float64:
		return int(x) == 1
	case json.Number:
		i, err := x.Int64()
		return err == nil && i == 1
	case string:
		return strings.TrimSpace(x) == "1"
	case bool:
		return x
	default:
		return false
	}
}

func parseTagGroups(raw any) [][]string {
	var entries []string
	switch v := raw.(type) {
	case []any:
		for _, x := range v {
			entries = append(entries, stringFromAny(x, ""))
		}
	case []string:
		entries = v
	case string:
		if v != "" {
			entries = []string{v}
		}
	}
	var groups [][]string
	for _, e := range entries {
		parts := strings.Split(e, "|")
		var g []string
		seen := map[string]struct{}{}
		for _, p := range parts {
			n := strings.TrimSpace(p)
			if n == "" {
				continue
			}
			if _, ok := seen[n]; ok {
				continue
			}
			seen[n] = struct{}{}
			g = append(g, n)
		}
		if len(g) > 0 {
			groups = append(groups, g)
		}
	}
	return groups
}

func flattenTagNames(raw any) []string {
	groups := parseTagGroups(raw)
	var out []string
	seen := map[string]struct{}{}
	for _, g := range groups {
		for _, n := range g {
			if _, ok := seen[n]; ok {
				continue
			}
			seen[n] = struct{}{}
			out = append(out, n)
		}
	}
	return out
}

func ageDaysFromPixiv(s *string) *float64 {
	if s == nil || strings.TrimSpace(*s) == "" {
		return nil
	}
	raw := strings.TrimSpace(*s)
	layouts := []string{
		time.RFC3339Nano,
		time.RFC3339,
		"2006-01-02T15:04:05",
		"2006-01-02 15:04:05",
		"2006-01-02",
	}
	var t time.Time
	var err error
	for _, layout := range layouts {
		t, err = time.Parse(layout, strings.ReplaceAll(raw, "Z", "+00:00"))
		if err == nil {
			break
		}
		t, err = time.ParseInLocation(layout, raw, time.UTC)
		if err == nil {
			break
		}
	}
	if err != nil {
		return nil
	}
	d := time.Since(t.UTC()).Hours() / 24.0
	if d < 0 {
		d = 0
	}
	return &d
}

func rngFromSeed(seed *string) *rand.Rand {
	if seed == nil || strings.TrimSpace(*seed) == "" {
		return rand.New(rand.NewSource(time.Now().UnixNano()))
	}
	h := fnv.New64a()
	_, _ = h.Write([]byte(*seed))
	return rand.New(rand.NewSource(int64(h.Sum64())))
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func intFromAny(v any, def int) int {
	switch x := v.(type) {
	case nil:
		return def
	case int:
		return x
	case int64:
		return int(x)
	case float64:
		return int(x)
	case json.Number:
		i, err := x.Int64()
		if err != nil {
			return def
		}
		return int(i)
	case string:
		i, err := strconv.Atoi(strings.TrimSpace(x))
		if err != nil {
			return def
		}
		return i
	case bool:
		if x {
			return 1
		}
		return 0
	default:
		return def
	}
}

func int64FromAny(v any, def int64) int64 {
	switch x := v.(type) {
	case nil:
		return def
	case int:
		return int64(x)
	case int64:
		return x
	case float64:
		return int64(x)
	case json.Number:
		i, err := x.Int64()
		if err != nil {
			return def
		}
		return i
	case string:
		i, err := strconv.ParseInt(strings.TrimSpace(x), 10, 64)
		if err != nil {
			return def
		}
		return i
	default:
		return def
	}
}

func floatFromAny(v any, def float64) float64 {
	switch x := v.(type) {
	case nil:
		return def
	case float64:
		return x
	case int:
		return float64(x)
	case int64:
		return float64(x)
	case json.Number:
		f, err := x.Float64()
		if err != nil {
			return def
		}
		return f
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(x), 64)
		if err != nil {
			return def
		}
		return f
	default:
		return def
	}
}

func stringFromAny(v any, def string) string {
	switch x := v.(type) {
	case nil:
		return def
	case string:
		return x
	case float64:
		return strconv.FormatInt(int64(x), 10)
	case int:
		return strconv.Itoa(x)
	case int64:
		return strconv.FormatInt(x, 10)
	case bool:
		if x {
			return "true"
		}
		return "false"
	default:
		return def
	}
}

func mapFromAny(v any) map[string]float64 {
	out := map[string]float64{}
	m, ok := v.(map[string]any)
	if !ok {
		return out
	}
	for k, val := range m {
		out[k] = floatFromAny(val, 0)
	}
	return out
}

func weightOr(m map[string]float64, k string, def float64) float64 {
	if m == nil {
		return def
	}
	if v, ok := m[k]; ok {
		return v
	}
	return def
}

func intOr(p *int, def int) int {
	if p == nil {
		return def
	}
	return *p
}
