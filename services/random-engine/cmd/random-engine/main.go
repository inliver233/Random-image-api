package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
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
	AddedAt        *string  `json:"added_at"`
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
	OK                   bool   `json:"ok"`
	Service              string `json:"service"`
	Ready                bool   `json:"ready"`
	IndexSize            int    `json:"index_size"`
	SnapshotRevision     string `json:"snapshot_revision"`
	SnapshotManifestHash string `json:"snapshot_manifest_hash"`
	CurrentStateHash     string `json:"current_state_hash"`
	StateVersion         uint64 `json:"state_version"`
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
	// initialized is set only by a complete, manifest-verified snapshot. Events
	// may update an initialized index, but can never turn a partial boot state ready.
	initialized          bool
	ready                bool
	snapshotRevision     string
	snapshotManifestHash string
	currentStateHash     string
	stateVersion         uint64
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
	// Prefer loopback in bare-metal dev; compose still maps host port intentionally.
	addr := envOr("RANDOM_ENGINE_ADDR", "127.0.0.1:8091")
	// Optional shared secret: when set, all non-/healthz routes require X-Engine-Secret.
	// Empty = open (dev/default-off dual-run); production compose should set a secret.
	engineSecret := strings.TrimSpace(os.Getenv("RANDOM_ENGINE_SECRET"))
	st := &engineState{
		revision:         "empty",
		currentStateHash: emptySnapshotContentHash(),
		byID:             map[int64]int{},
		tagIndex:         map[string]map[int64]struct{}{},
		clientDedup:      newClientDedupStore(),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		st.mu.RLock()
		defer st.mu.RUnlock()
		writeJSON(w, http.StatusOK, healthResponse{
			OK:                   true,
			Service:              "random-engine",
			Ready:                st.ready,
			IndexSize:            len(st.byKey),
			SnapshotRevision:     st.snapshotRevision,
			SnapshotManifestHash: st.snapshotManifestHash,
			CurrentStateHash:     st.currentStateHash,
			StateVersion:         st.stateVersion,
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

	var handler http.Handler = mux
	if engineSecret != "" {
		handler = requireEngineSecret(mux, engineSecret)
		log.Printf("random-engine auth: X-Engine-Secret required (non-healthz)")
	} else {
		log.Printf("random-engine auth: open (RANDOM_ENGINE_SECRET unset)")
	}
	log.Printf("random-engine listening on %s", addr)
	if err := http.ListenAndServe(addr, handler); err != nil {
		log.Fatal(err)
	}
}

// requireEngineSecret gates pick/admin routes when RANDOM_ENGINE_SECRET is set.
// /healthz stays open for compose healthchecks.
func requireEngineSecret(next http.Handler, expected string) http.Handler {
	expected = strings.TrimSpace(expected)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if path == "/healthz" || path == "/" {
			next.ServeHTTP(w, r)
			return
		}
		got := strings.TrimSpace(r.Header.Get("X-Engine-Secret"))
		if expected == "" || !timingSafeEqual(got, expected) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func timingSafeEqual(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	var diff byte
	for i := 0; i < len(a); i++ {
		diff |= a[i] ^ b[i]
	}
	return diff == 0
}

func handleSnapshot(w http.ResponseWriter, r *http.Request, st *engineState) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Revision         string             `json:"revision"`
		Complete         bool               `json:"complete"`
		ExpectedCount    *int               `json:"expected_count"`
		ContentHash      string             `json:"content_hash"`
		BaseStateVersion *uint64            `json:"base_state_version"`
		Images           []indexImage       `json:"images"`
		TagPostings      map[string][]int64 `json:"tag_postings"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}

	rev := strings.TrimSpace(body.Revision)
	if rev == "" || !body.Complete || body.ExpectedCount == nil || body.BaseStateVersion == nil {
		http.Error(w, "complete snapshot manifest required", http.StatusBadRequest)
		return
	}
	if *body.ExpectedCount < 0 || len(body.Images) != *body.ExpectedCount {
		http.Error(w, "snapshot count mismatch", http.StatusBadRequest)
		return
	}
	calculatedHash, err := snapshotContentHash(body.Images, body.TagPostings)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if !strings.EqualFold(strings.TrimSpace(body.ContentHash), calculatedHash) {
		http.Error(w, "snapshot content hash mismatch", http.StatusBadRequest)
		return
	}

	// A formal snapshot contains enabled catalog rows only. Silently filtering a
	// disabled row would make a non-empty partial index look complete and ready.
	imgs := make([]indexImage, 0, len(body.Images))
	for _, im := range body.Images {
		if !isFormalSnapshotEnabledStatus(im.Status) {
			http.Error(w, "snapshot contains disabled image", http.StatusBadRequest)
			return
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
			if index, ok := byID[id]; ok {
				tagIndex[k][id] = struct{}{}
				found := false
				for _, existing := range imgs[index].TagNames {
					if existing == k {
						found = true
						break
					}
				}
				if !found {
					imgs[index].TagNames = append(imgs[index].TagNames, k)
					sort.Strings(imgs[index].TagNames)
				}
			}
		}
	}
	currentStateHash, err := snapshotContentHash(imgs, nil)
	if err != nil {
		http.Error(w, "snapshot state hash failed", http.StatusBadRequest)
		return
	}

	st.mu.Lock()
	if st.stateVersion != *body.BaseStateVersion {
		currentVersion := st.stateVersion
		st.mu.Unlock()
		writeJSON(w, http.StatusConflict, map[string]any{
			"ok":                     false,
			"code":                   "STALE_SNAPSHOT",
			"expected_state_version": *body.BaseStateVersion,
			"current_state_version":  currentVersion,
		})
		return
	}
	st.byKey = imgs
	st.byID = byID
	st.tagIndex = tagIndex
	st.revision = rev
	st.initialized = true
	st.ready = len(imgs) > 0
	st.snapshotRevision = rev
	st.snapshotManifestHash = calculatedHash
	st.currentStateHash = currentStateHash
	st.stateVersion++
	size := len(imgs)
	ready := st.ready
	stateVersion := st.stateVersion
	st.mu.Unlock()

	writeJSON(w, http.StatusOK, map[string]any{
		"ok":            true,
		"ready":         ready,
		"index_size":    size,
		"revision":      rev,
		"content_hash":  calculatedHash,
		"state_version": stateVersion,
	})
}

func emptySnapshotContentHash() string {
	hash, err := snapshotContentHash(nil, nil)
	if err != nil {
		panic(err)
	}
	return hash
}

func snapshotB64(value string) string {
	return base64.StdEncoding.EncodeToString([]byte(value))
}

func snapshotOptionalString(value *string) any {
	if value == nil {
		return nil
	}
	return snapshotB64(*value)
}

func snapshotOptionalInt(value *int) any {
	if value == nil {
		return nil
	}
	return *value
}

func snapshotOptionalInt64(value *int64) any {
	if value == nil {
		return nil
	}
	return *value
}

// snapshotContentHash hashes the complete normalized payload through a fixed
// JSON array schema shared with Python. Strings are base64 and floats use their
// IEEE-754 bits, avoiding JSON escaping/formatting differences across languages.
func snapshotContentHash(images []indexImage, tagPostings map[string][]int64) (string, error) {
	rows := make([][]any, 0, len(images))
	seen := make(map[int64]struct{}, len(images))
	for _, im := range images {
		if im.ID <= 0 {
			return "", fmt.Errorf("snapshot contains invalid image id")
		}
		if _, ok := seen[im.ID]; ok {
			return "", fmt.Errorf("snapshot contains duplicate image id")
		}
		seen[im.ID] = struct{}{}
		if im.IllustID <= 0 {
			return "", fmt.Errorf("snapshot contains invalid illust_id")
		}
		if im.PageIndex < 0 {
			return "", fmt.Errorf("snapshot contains invalid page_index")
		}
		ext := strings.ToLower(strings.TrimSpace(im.Ext))
		if ext != im.Ext || (ext != "jpg" && ext != "jpeg" && ext != "png" && ext != "gif" && ext != "webp" && ext != "zip") {
			return "", fmt.Errorf("snapshot contains invalid ext")
		}
		if !isFormalSnapshotEnabledStatus(im.Status) {
			return "", fmt.Errorf("snapshot contains disabled image")
		}
		if math.IsNaN(im.RandomKey) || math.IsInf(im.RandomKey, 0) || im.RandomKey < 0 || im.RandomKey >= 1 {
			return "", fmt.Errorf("snapshot contains invalid random_key")
		}

		tagNames := append([]string(nil), im.TagNames...)
		sort.Strings(tagNames)
		encodedTagNames := make([]string, 0, len(tagNames))
		previousName := ""
		for _, name := range tagNames {
			if name == "" || strings.TrimSpace(name) != name || name == previousName {
				return "", fmt.Errorf("snapshot contains invalid tag_names")
			}
			previousName = name
			encodedTagNames = append(encodedTagNames, snapshotB64(name))
		}
		tagIDs := append([]int64{}, im.TagIDs...)
		sort.Slice(tagIDs, func(i, j int) bool { return tagIDs[i] < tagIDs[j] })
		for i, tagID := range tagIDs {
			if tagID <= 0 || (i > 0 && tagID == tagIDs[i-1]) {
				return "", fmt.Errorf("snapshot contains invalid tag_ids")
			}
		}
		rows = append(rows, []any{
			im.ID, im.IllustID, im.PageIndex, snapshotB64(im.Ext), 1,
			fmt.Sprintf("%016x", math.Float64bits(im.RandomKey)),
			snapshotOptionalInt(im.Width), snapshotOptionalInt(im.Height), snapshotOptionalInt(im.Orientation),
			snapshotOptionalInt(im.XRestrict), snapshotOptionalInt(im.AIType), snapshotOptionalInt(im.IllustType),
			snapshotOptionalInt64(im.UserID), snapshotOptionalString(im.UserName), snapshotOptionalString(im.Title),
			snapshotOptionalString(im.CreatedAtPixiv), snapshotOptionalString(im.AddedAt),
			snapshotOptionalInt(im.BookmarkCount), snapshotOptionalInt(im.ViewCount), snapshotOptionalInt(im.CommentCount),
			snapshotOptionalString(im.OriginalURL), encodedTagNames, tagIDs,
			snapshotOptionalString(im.LastFailAt), snapshotOptionalString(im.LastErrorCode),
		})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i][0].(int64) < rows[j][0].(int64) })

	postingRows := make([][]any, 0, len(tagPostings))
	for name, postingIDs := range tagPostings {
		if name == "" || strings.TrimSpace(name) != name {
			return "", fmt.Errorf("snapshot contains invalid tag_postings")
		}
		ids := append([]int64{}, postingIDs...)
		sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
		for i, id := range ids {
			if _, ok := seen[id]; !ok {
				return "", fmt.Errorf("snapshot tag_postings reference unknown image")
			}
			if i > 0 && id == ids[i-1] {
				return "", fmt.Errorf("snapshot contains invalid tag_postings")
			}
		}
		postingRows = append(postingRows, []any{snapshotB64(name), ids})
	}
	sort.Slice(postingRows, func(i, j int) bool { return postingRows[i][0].(string) < postingRows[j][0].(string) })
	canonical, err := json.Marshal([]any{rows, postingRows})
	if err != nil {
		return "", fmt.Errorf("snapshot canonical encoding failed: %w", err)
	}
	return fmt.Sprintf("%x", sha256.Sum256(canonical)), nil
}

func isFormalSnapshotEnabledStatus(v any) bool {
	switch value := v.(type) {
	case int:
		return value == 1
	case int64:
		return value == 1
	case float64:
		return value == 1
	case json.Number:
		parsed, err := value.Int64()
		return err == nil && parsed == 1
	default:
		return false
	}
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
	if contentHash, err := snapshotContentHash(imgs, nil); err == nil {
		st.currentStateHash = contentHash
	}
	st.revision = time.Now().UTC().Format(time.RFC3339Nano)
	if applied > 0 {
		st.stateVersion++
	}
	st.ready = st.initialized && len(imgs) > 0

	writeJSON(w, http.StatusOK, map[string]any{
		"ok":            true,
		"applied":       applied,
		"ready":         st.ready,
		"index_size":    len(imgs),
		"revision":      st.revision,
		"state_version": st.stateVersion,
	})
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

	if !st.ready {
		// Distinct from filter NO_MATCH so BFF dual-run can warm/snapshot before cutover.
		resp := pickResponse{OK: true, Code: "INDEX_NOT_READY", Items: []pickItem{}}
		if req.Debug {
			resp.Debug = map[string]any{"reason": "index_not_ready", "revision": st.revision, "index_size": len(st.byKey)}
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
		pickMode := stringFromAny(req.Quality["pick_mode"], "weighted")
		temp := floatFromAny(req.Quality["temperature"], 1.0)
		weights := mapFromAny(req.Quality["weights"])
		multipliers := mapFromAny(req.Quality["multipliers"])
		halfLife := floatFromAny(req.Quality["freshness_half_life_days"], 21.0)
		velSmooth := floatFromAny(req.Quality["velocity_smooth_days"], 2.0)

		imgPen := floatFromAny(req.Quality["dedup_image_penalty"], 0)
		authPen := floatFromAny(req.Quality["dedup_author_penalty"], 0)
		recentImgs := int64SetFromAny(req.Quality["recent_image_ids"])
		recentAuths := int64SetFromAny(req.Quality["recent_author_ids"])

		// Python pick_by_quality drops zero-multiplier types in SQL (ai_type_allowed /
		// illust_type_allowed) before sampling. Pre-filter so zero-mult classes do not
		// consume quality sample slots (post-score skip alone is not enough).
		allowed := filterByMultiplierAllow(candidates, multipliers)
		if samples > len(allowed) {
			samples = len(allowed)
		}
		pool := ringSample(allowed, samples, rng)
		scored := make([]scoredImg, 0, len(pool))
		for _, im := range pool {
			// Temperature scales score only; log(mult) is added after (Python parity).
			// Python pick_by_quality skips multiplier<=0 candidates entirely (no 1e-9 clamp).
			logit, dbg, ok := qualityLogit(im, weights, multipliers, halfLife, velSmooth, temp, recentImgs, recentAuths, imgPen, authPen)
			if !ok {
				continue
			}
			scored = append(scored, scoredImg{im: im, logit: logit, dbg: dbg})
		}
		// Logits already include /temperature; softmax must not divide again.
		chosen := pickFromScored(scored, pickMode, req.Limit, rng)
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

func pickFromScored(scored []scoredImg, pickMode string, limit int, rng *rand.Rand) []scoredImg {
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
	// Softmax over already temperature-scaled logits (Python: exp(logit - max)).
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
			w := math.Exp(s.logit - maxL)
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

// filterByMultiplierAllow mirrors Python pick_by_quality ai_type_allowed /
// illust_type_allowed via allowed_int_or_null_clause (NOT imageMultiplier).
//
// Python builds allow sets as:
//
//	ai: 1 | non_ai: 0 | unknown_ai: NULL only
//	illust: 0 | manga: 1 | ugoira: 2 | unknown_illust_type: NULL only
//
// Then allowed_int_or_null_clause returns NO clause when the set is "complete"
// ({0,1}+NULL for AI, {0,1,2}+NULL for illust) — so out-of-range values still
// sample. Partial sets filter exact members only (out-of-range excluded).
// Empty allow-set → no rows. imageMultiplier still maps residual out-of-range
// values to unknown_* for scoring after sample.
func filterByMultiplierAllow(cands []indexImage, multipliers map[string]float64) []indexImage {
	allowAI1 := weightOr(multipliers, "ai", 1.0) > 0
	allowAI0 := weightOr(multipliers, "non_ai", 1.0) > 0
	allowAINull := weightOr(multipliers, "unknown_ai", 1.0) > 0
	allowIllust0 := weightOr(multipliers, "illust", 1.0) > 0
	allowIllust1 := weightOr(multipliers, "manga", 1.0) > 0
	allowIllust2 := weightOr(multipliers, "ugoira", 1.0) > 0
	allowIllustNull := weightOr(multipliers, "unknown_illust_type", 1.0) > 0
	// Python: if either allow-set is empty, yield no rows.
	if !(allowAI1 || allowAI0 || allowAINull) || !(allowIllust0 || allowIllust1 || allowIllust2 || allowIllustNull) {
		return nil
	}
	// allowed_int_or_null_clause: complete {0,1}+NULL (AI) or {0,1,2}+NULL (illust)
	// returns no SQL filter — keep every value including out-of-range.
	aiOpen := allowAI0 && allowAI1 && allowAINull
	illustOpen := allowIllust0 && allowIllust1 && allowIllust2 && allowIllustNull
	out := make([]indexImage, 0, len(cands))
	for _, im := range cands {
		if !aiOpen {
			if im.AIType == nil {
				if !allowAINull {
					continue
				}
			} else if *im.AIType == 1 {
				if !allowAI1 {
					continue
				}
			} else if *im.AIType == 0 {
				if !allowAI0 {
					continue
				}
			} else {
				// Partial set: non-0/1 not in allow list (unknown_ai is NULL-only).
				continue
			}
		}
		if !illustOpen {
			if im.IllustType == nil {
				if !allowIllustNull {
					continue
				}
			} else if *im.IllustType == 0 {
				if !allowIllust0 {
					continue
				}
			} else if *im.IllustType == 1 {
				if !allowIllust1 {
					continue
				}
			} else if *im.IllustType == 2 {
				if !allowIllust2 {
					continue
				}
			} else {
				// Partial set: non-0/1/2 not in allow list.
				continue
			}
		}
		out = append(out, im)
	}
	return out
}

// imageMultiplier computes the product of AI + illust-type multipliers (defaults 1).
func imageMultiplier(im indexImage, multipliers map[string]float64) float64 {
	// Align Python multiplier_for_image: ai 1 / non_ai 0 / else unknown_ai;
	// illust 0 / manga 1 / ugoira 2 / else unknown_illust_type.
	mult := 1.0
	if im.AIType == nil {
		mult *= weightOr(multipliers, "unknown_ai", 1.0)
	} else if *im.AIType == 1 {
		mult *= weightOr(multipliers, "ai", 1.0)
	} else if *im.AIType == 0 {
		mult *= weightOr(multipliers, "non_ai", 1.0)
	} else {
		mult *= weightOr(multipliers, "unknown_ai", 1.0)
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
	return mult
}

func qualityLogit(im indexImage, weights, multipliers map[string]float64, halfLifeDays, velSmooth, temperature float64, recentImageIDs, recentAuthorIDs map[int64]struct{}, imagePenalty, authorPenalty float64) (float64, map[string]any, bool) {
	wBookmark := weightOr(weights, "bookmark", 4.0)
	wView := weightOr(weights, "view", 0.5)
	wComment := weightOr(weights, "comment", 2.0)
	wPixels := weightOr(weights, "pixels", 1.0)
	wRate := weightOr(weights, "bookmark_rate", 3.0)
	wFresh := weightOr(weights, "freshness", 1.0)
	wVel := weightOr(weights, "bookmark_velocity", 1.2)

	// Align Python as_nonneg_int for counters / dims used in quality_score.
	bm := float64(nonnegInt(im.BookmarkCount))
	vw := float64(nonnegInt(im.ViewCount))
	cm := float64(nonnegInt(im.CommentCount))
	pixels := 0.0
	w := nonnegInt(im.Width)
	h := nonnegInt(im.Height)
	if w > 0 && h > 0 {
		pixels = float64(w) * float64(h)
	}
	rate := 0.0
	if vw > 0 {
		rate = bm / vw
	}

	// Freshness: Python falls back to added_at when created_at_pixiv missing.
	// Velocity: Python uses created_at_pixiv only (no added_at fallback).
	ageFresh := ageDaysFromPixiv(im.CreatedAtPixiv)
	if ageFresh == nil {
		ageFresh = ageDaysFromPixiv(im.AddedAt)
	}
	ageVel := ageDaysFromPixiv(im.CreatedAtPixiv)
	// Align Python score_image_with_time_boosts freshness: -age/half_life (not exp half-life).
	fresh := 0.0
	if ageFresh != nil && halfLifeDays > 0 {
		fresh = -(*ageFresh) / halfLifeDays
	}
	vel := 0.0
	if ageVel != nil {
		// Python: denom = age + smooth; log1p(bm / max(1.0, denom)).
		den := *ageVel + math.Max(0.0, velSmooth)
		if den < 1.0 {
			den = 1.0
		}
		vel = bm / den
	}

	// Python score_image_with_time_boosts total (base + freshness_contrib + velocity_contrib).
	score := wBookmark*math.Log1p(bm) +
		wView*math.Log1p(vw) +
		wComment*math.Log1p(cm) +
		wPixels*math.Log1p(pixels/1_000_000.0) +
		wRate*math.Log1p(rate*1000.0) +
		wFresh*fresh +
		wVel*math.Log1p(vel)

	mult := imageMultiplier(im, multipliers)
	if mult <= 0 {
		// Python pick_by_quality: continue (drop candidate); do not clamp to epsilon.
		return 0, nil, false
	}
	if temperature <= 0 {
		temperature = 1
	}
	// Python: logit = score / temperature + log(multiplier)
	logit := score/temperature + math.Log(mult)
	// Soft anti-repeat (Python pick_by_quality): subtract penalties after base logit.
	if imagePenalty != 0 && recentImageIDs != nil {
		if _, ok := recentImageIDs[im.ID]; ok {
			logit -= imagePenalty
		}
	}
	if authorPenalty != 0 && recentAuthorIDs != nil && im.UserID != nil {
		if _, ok := recentAuthorIDs[*im.UserID]; ok {
			logit -= authorPenalty
		}
	}

	dbg := map[string]any{
		"logit":       logit,
		"score":       score,
		"temperature": temperature,
		"bookmark":    bm,
		"view":        vw,
		"freshness":   fresh,
		"velocity":    vel,
		"multiplier":  mult,
	}
	return logit, dbg, true
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
			// Align Python orientation_where_clause: Image.orientation == N only.
			// Do not derive from width/height — null orientation never matches in SQL.
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
				if im.Orientation == nil || *im.Orientation != wantOri {
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

func int64SetFromAny(v any) map[int64]struct{} {
	out := map[int64]struct{}{}
	switch x := v.(type) {
	case nil:
		return out
	case []any:
		for _, el := range x {
			id := int64FromAny(el, 0)
			if id > 0 {
				out[id] = struct{}{}
			}
		}
	case []int64:
		for _, id := range x {
			if id > 0 {
				out[id] = struct{}{}
			}
		}
	case []int:
		for _, id := range x {
			if id > 0 {
				out[int64(id)] = struct{}{}
			}
		}
	}
	return out
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

// nonnegInt mirrors Python as_nonneg_int: nil / non-positive → 0.
func nonnegInt(p *int) int {
	if p == nil || *p <= 0 {
		return 0
	}
	return *p
}
