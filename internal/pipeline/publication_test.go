package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testEmptySnapshot(t *testing.T) string {
	t.Helper()
	server := httptest.NewServer(http.NotFoundHandler())
	defer server.Close()
	root := filepath.Join(t.TempDir(), "baseline")
	identity, err := Snapshot(context.Background(), server.URL+"/viewer/resources/file", root)
	if err != nil || identity.BaselineChannelSHA256 != "absent" {
		t.Fatalf("empty snapshot: %+v %v", identity, err)
	}
	return root
}

func testResourceServer(t *testing.T, candidate string) *httptest.Server {
	t.Helper()
	_, raw, err := manifestAt(filepath.Join(candidate, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	sha := digest(raw)
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Query().Get("path")
		if !publicPath(path) {
			http.Error(w, "invalid path", 400)
			return
		}
		if path == "channels/stable.json" {
			w.Write(pointerBytes(sha))
			return
		}
		if path == "releases/"+sha+".json" {
			w.Write(raw)
			return
		}
		file, err := safeRepoFile(candidate, path, false)
		if err != nil {
			http.NotFound(w, r)
			return
		}
		http.ServeFile(w, r, file)
	}))
}

func testSnapshotCandidate(t *testing.T, candidate string) string {
	t.Helper()
	server := testResourceServer(t, candidate)
	defer server.Close()
	root := filepath.Join(t.TempDir(), "baseline")
	if _, err := Snapshot(context.Background(), server.URL+"/viewer/resources/file", root); err != nil {
		t.Fatal(err)
	}
	return root
}

func TestResourceSnapshotReviewAndPlanWithoutGit(t *testing.T) {
	first := testCandidate(t, "1", "Copper")
	empty := testEmptySnapshot(t)
	review, file, sha := testReview(t, first, empty)
	if review.Schema != 2 || review.BaselineChannelSHA256 != "absent" || review.Families["items"].Added != 2 {
		t.Fatalf("initial review: %+v", review)
	}
	output := filepath.Join(t.TempDir(), "publication-plan.json")
	plan, err := PreparePublication(first, empty, file, sha, output)
	if err != nil {
		t.Fatal(err)
	}
	channel, err := os.ReadFile(filepath.Join(first, "channels", "stable.json"))
	if err != nil || digest(channel) != plan.Channel.SHA256 || plan.Channel.Bytes != int64(len(channel)) {
		t.Fatalf("channel: %v", err)
	}
	if err := os.Remove(output); err != nil {
		t.Fatal(err)
	}
	if _, err := PreparePublication(first, empty, file, sha, output); err != nil {
		t.Fatal("deterministic retry", err)
	}
	baseline := testSnapshotCandidate(t, first)
	unchanged, _, _ := testReview(t, first, baseline)
	if unchanged.Families["items"].Added+unchanged.Families["items"].Removed+unchanged.Families["items"].Changed != 0 || unchanged.Textures.Changed != 0 {
		t.Fatalf("identical resource review: %+v", unchanged)
	}
	second := testCandidate(t, "2", "Iron")
	changed, _, _ := testReview(t, second, baseline)
	items := changed.Families["items"]
	if items.Added != 1 || items.Removed != 1 || items.Changed != 1 || items.ChangedFields["name"] != 1 || changed.Textures.Changed != 1 {
		t.Fatalf("semantic changes: %+v", changed)
	}
	if changed.BaselineManifestSHA256 != plan.CandidateManifestSHA256 {
		t.Fatal("baseline manifest binding lost")
	}
}

func TestBundledResourcePlanHasNoDuplicatePNG(t *testing.T) {
	candidate, _, _ := testBundledCandidate(t)
	baseline := testEmptySnapshot(t)
	_, file, sha := testReview(t, candidate, baseline)
	plan, err := PreparePublication(candidate, baseline, file, sha, filepath.Join(t.TempDir(), "plan.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, object := range plan.Objects {
		if strings.HasSuffix(object.Path, ".png") {
			t.Fatal("raw PNG duplicated alongside image bundles")
		}
	}
	if _, err := ComputeReview(candidate, testSnapshotCandidate(t, candidate)); err != nil {
		t.Fatal("bundled snapshot transitive verification", err)
	}
}

func TestResourceApprovalRejectsMutation(t *testing.T) {
	for _, change := range []string{"review", "detail", "candidate", "baseline", "legacy", "missing"} {
		t.Run(change, func(t *testing.T) {
			candidate := testCandidate(t, "1", "Copper")
			baseline := testEmptySnapshot(t)
			_, file, sha := testReview(t, candidate, baseline)
			switch change {
			case "review":
				os.WriteFile(file, []byte("{}"), 0600)
			case "detail":
				os.WriteFile(filepath.Join(filepath.Dir(file), "review-changes.ndjson"), []byte("{}\n"), 0600)
			case "candidate", "missing":
				m, _, _ := manifestAt(filepath.Join(candidate, "manifest.json"))
				m.GameVersion = "different"
				if change == "missing" {
					m.Missing = []string{"required family"}
				}
				raw, _ := json.Marshal(m)
				os.WriteFile(filepath.Join(candidate, "manifest.json"), raw, 0600)
			case "baseline":
				os.WriteFile(filepath.Join(baseline, "channels", "stable.json"), pointerBytes(strings.Repeat("a", 64)), 0600)
			case "legacy":
				raw, _ := os.ReadFile(file)
				var m map[string]any
				json.Unmarshal(raw, &m)
				m["schema"] = 1
				raw, _ = json.Marshal(m)
				os.WriteFile(file, raw, 0600)
				sha = digest(raw)
			}
			if _, err := PreparePublication(candidate, baseline, file, sha, filepath.Join(t.TempDir(), "plan.json")); err == nil {
				t.Fatal("mutated approval accepted")
			}
		})
	}
}

func TestSnapshotFailureNeverBecomesFirstPublication(t *testing.T) {
	for _, mode := range []string{"401", "403", "500", "redirect", "invalid", "oversized", "encoded", "wrong-release", "bad-object", "cancelled"} {
		t.Run(mode, func(t *testing.T) {
			candidate := testCandidate(t, "1", "Copper")
			valid := testResourceServer(t, candidate)
			defer valid.Close()
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				path := r.URL.Query().Get("path")
				switch mode {
				case "401":
					w.WriteHeader(401)
					return
				case "403":
					w.WriteHeader(403)
					return
				case "500":
					w.WriteHeader(500)
					return
				case "redirect":
					http.Redirect(w, r, valid.URL, 302)
					return
				case "invalid":
					w.Write([]byte("{}"))
					return
				case "oversized":
					w.Write(bytes.Repeat([]byte{'x'}, 4097))
					return
				case "encoded":
					w.Header().Set("Content-Encoding", "gzip")
					w.Write([]byte("invalid"))
					return
				case "wrong-release":
					if strings.HasPrefix(path, "releases/") {
						w.Write([]byte("{}"))
						return
					}
				case "bad-object":
					if strings.HasPrefix(path, "objects/") {
						w.Write([]byte("bad object"))
						return
					}
				}
				response, err := http.Get(valid.URL + r.URL.RequestURI())
				if err != nil {
					w.WriteHeader(500)
					return
				}
				defer response.Body.Close()
				w.WriteHeader(response.StatusCode)
				io.Copy(w, response.Body)
			}))
			defer server.Close()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "cancelled" {
				cancel()
			}
			root := filepath.Join(t.TempDir(), "baseline")
			if _, err := Snapshot(ctx, server.URL+"/viewer/resources/file", root); err == nil {
				t.Fatal("bad snapshot accepted")
			}
			if _, err := snapshotIdentity(root); err == nil {
				t.Fatal("failed snapshot became valid absent baseline")
			}
		})
	}
}

func TestPublicPathsRejectPrivateAndTraversal(t *testing.T) {
	sha := strings.Repeat("a", 64)
	for _, path := range []string{"../private-saves/a", "objects/bb/" + sha + ".png", "objects/aa/" + sha + ".exe", "channels/../stable.json", "channels\\stable.json", "https://example.com/", "releases/%2e%2e.json", "/channels/stable.json"} {
		if publicPath(path) {
			t.Fatal("unsafe public path", path)
		}
	}
}

func TestInterruptedExactOutputCanRetryWithoutReplacingOtherBytes(t *testing.T) {
	root := t.TempDir()
	// Emulate process death while writing its same-directory temporary file.
	if err := os.WriteFile(filepath.Join(root, ".resource-write-interrupted"), []byte("half"), 0600); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "plan.json")
	raw := []byte("complete approval-bound plan\n")
	if err := writeExactFile(target, raw); err != nil {
		t.Fatal(err)
	}
	if err := writeExactFile(target, raw); err != nil {
		t.Fatal("idempotent retry", err)
	}
	if err := writeExactFile(target, []byte("different")); err == nil {
		t.Fatal("replaced another writer's bytes")
	}
	actual, err := os.ReadFile(target)
	if err != nil || !bytes.Equal(actual, raw) {
		t.Fatal("original output changed", err)
	}
}
