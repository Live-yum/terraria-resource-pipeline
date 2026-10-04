package pipeline

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"terraria-resource-pipeline/internal/artifact"
)

var publicObjectPath = regexp.MustCompile(`^objects/([0-9a-f]{2})/([0-9a-f]{64})\.(json\.gz|png|zip|zip\.gz|txci\.gz|srgb\.gz|bin\.gz|json|bin)$`)

type SnapshotIdentity struct {
	Schema                 int    `json:"schema"`
	BaselineChannelSHA256  string `json:"baselineChannelSha256"`
	BaselineManifestSHA256 string `json:"baselineManifestSha256"`
}

type PublicationFile struct {
	Path           string `json:"path"`
	SHA256         string `json:"sha256"`
	Bytes          int64  `json:"bytes"`
	ManifestSHA256 string `json:"manifestSha256,omitempty"`
}

type PublicationPlan struct {
	Schema                  int               `json:"schema"`
	CandidateManifestSHA256 string            `json:"candidateManifestSha256"`
	ReviewSHA256            string            `json:"reviewSha256"`
	BaselineManifestSHA256  string            `json:"baselineManifestSha256"`
	BaselineChannelSHA256   string            `json:"baselineChannelSha256"`
	Objects                 []PublicationFile `json:"objects"`
	Release                 PublicationFile   `json:"release"`
	Channel                 PublicationFile   `json:"channel"`
}

func publicPath(path string) bool {
	if path == "channels/stable.json" {
		return true
	}
	if len(path) == len("releases/")+64+len(".json") && path[:len("releases/")] == "releases/" && path[len(path)-5:] == ".json" {
		return validSHA(path[len("releases/") : len(path)-5])
	}
	parts := publicObjectPath.FindStringSubmatch(path)
	return parts != nil && parts[1] == parts[2][:2]
}

func snapshotIdentity(root string) (SnapshotIdentity, error) {
	var identity SnapshotIdentity
	path, err := safeRepoFile(root, ".snapshot.json", false)
	if err != nil {
		return identity, err
	}
	raw, err := readSmallRegular(path, 4096)
	if err != nil {
		return identity, fmt.Errorf("a completed baseline snapshot is required: %w", err)
	}
	if err := json.Unmarshal(raw, &identity); err != nil || identity.Schema != 1 {
		return identity, errors.New("invalid baseline snapshot identity")
	}
	channel, err := safeRepoFile(root, "channels/stable.json", false)
	if identity.BaselineChannelSHA256 == "absent" {
		if identity.BaselineManifestSHA256 != "" {
			return identity, errors.New("absent channel has a manifest")
		}
		if err == nil {
			_, err = os.Lstat(channel)
		}
		if !os.IsNotExist(err) {
			return identity, errors.New("absent snapshot contains a channel or unsafe path")
		}
		return identity, nil
	}
	if err != nil || !validSHA(identity.BaselineChannelSHA256) || !validSHA(identity.BaselineManifestSHA256) {
		return identity, errors.New("invalid baseline snapshot hashes")
	}
	raw, err = readSmallRegular(channel, 4096)
	if err != nil || digest(raw) != identity.BaselineChannelSHA256 {
		return identity, errors.New("baseline channel changed since snapshot")
	}
	_, sha, err := repoBaseline(root)
	if err != nil || sha != identity.BaselineManifestSHA256 {
		return identity, errors.New("baseline manifest changed since snapshot")
	}
	return identity, nil
}

// Snapshot reads the public backend proxy only. It never receives AList credentials.
// The identity is written last, so an interrupted download cannot become a baseline.
func Snapshot(ctx context.Context, sourceURL, output string) (SnapshotIdentity, error) {
	var identity SnapshotIdentity
	endpoint, err := url.Parse(sourceURL)
	if err != nil || endpoint.Host == "" || (endpoint.Scheme != "https" && endpoint.Scheme != "http") || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return identity, errors.New("source-url must be a public HTTP(S) endpoint without credentials, query or fragment")
	}
	if err := os.Mkdir(output, 0700); err != nil {
		return identity, err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DisableCompression = true
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 60 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("resource proxy must not redirect") }}
	fetch := func(relative string, limit int64, expected string, size int64) (bool, error) {
		if !publicPath(relative) || limit < 0 || limit > artifact.MaxBytes {
			return false, errors.New("invalid public resource path or budget")
		}
		target, err := safeRepoFile(output, relative, true)
		if err != nil {
			return false, err
		}
		u := *endpoint
		query := u.Query()
		query.Set("path", relative)
		u.RawQuery = query.Encode()
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
		if err != nil {
			return false, err
		}
		response, err := client.Do(request)
		if err != nil {
			return false, err
		}
		defer response.Body.Close()
		if response.StatusCode == http.StatusNotFound && relative == "channels/stable.json" {
			return true, nil
		}
		if response.StatusCode != http.StatusOK || response.Header.Get("Content-Encoding") != "" {
			return false, fmt.Errorf("resource proxy refused %s: status %d or encoded response", relative, response.StatusCode)
		}
		file, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return false, err
		}
		hash := sha256.New()
		count, copyErr := io.CopyBuffer(io.MultiWriter(file, hash), io.LimitReader(response.Body, limit+1), make([]byte, 64<<10))
		closeErr := file.Close()
		if copyErr != nil || closeErr != nil || count > limit || (size >= 0 && count != size) || (expected != "" && hex.EncodeToString(hash.Sum(nil)) != expected) {
			return false, fmt.Errorf("resource %s size/digest/download mismatch: %w", relative, errors.Join(copyErr, closeErr))
		}
		return false, nil
	}
	absent, err := fetch("channels/stable.json", 4096, "", -1)
	if err != nil {
		return identity, err
	}
	identity = SnapshotIdentity{Schema: 1, BaselineChannelSHA256: "absent"}
	if !absent {
		channel, err := readSmallRegular(filepath.Join(output, "channels", "stable.json"), 4096)
		if err != nil {
			return identity, err
		}
		var pointer stablePointer
		if err := json.Unmarshal(channel, &pointer); err != nil || !validSHA(pointer.ManifestSHA256) {
			return identity, errors.New("invalid remote resource channel")
		}
		releasePath := "releases/" + pointer.ManifestSHA256 + ".json"
		if _, err := fetch(releasePath, 2<<20, pointer.ManifestSHA256, -1); err != nil {
			return identity, err
		}
		manifest, raw, err := manifestAt(filepath.Join(output, filepath.FromSlash(releasePath)))
		if err != nil {
			return identity, err
		}
		if _, err := fetch(manifest.Textures.Path, manifest.Textures.Bytes, manifest.Textures.SHA256, manifest.Textures.Bytes); err != nil {
			return identity, err
		}
		objects, err := manifestObjects(output, manifest)
		if err != nil {
			return identity, err
		}
		if len(objects) > 65536 {
			return identity, errors.New("baseline object count exceeds budget")
		}
		var total int64
		for _, object := range objects {
			if object.Bytes < 0 || object.Bytes > artifact.MaxBytes || object.Bytes > (1<<30)-total {
				return identity, errors.New("baseline byte budget exceeded")
			}
			total += object.Bytes
			if object.Path != manifest.Textures.Path {
				if _, err := fetch(object.Path, object.Bytes, object.SHA256, object.Bytes); err != nil {
					return identity, err
				}
			}
			if err := artifact.Verify(output, object); err != nil {
				return identity, err
			}
		}
		if err := writeExactFile(filepath.Join(output, "manifest.json"), raw); err != nil {
			return identity, err
		}
		if err := Verify(output); err != nil {
			return identity, err
		}
		identity.BaselineChannelSHA256, identity.BaselineManifestSHA256 = digest(channel), pointer.ManifestSHA256
	}
	raw, err := json.Marshal(identity)
	if err != nil {
		return identity, err
	}
	return identity, writeExactFile(filepath.Join(output, ".snapshot.json"), append(raw, '\n'))
}

func pointerBytes(manifestSHA string) []byte {
	data, _ := json.Marshal(stablePointer{ManifestSHA256: manifestSHA})
	return append(data, '\n')
}

// Only exact reproducible outputs can be reused after an interrupted preparation.
func writeExactFile(path string, raw []byte) error {
	if existing, err := readSmallRegular(path, int64(len(raw))); err == nil {
		if bytes.Equal(existing, raw) {
			return nil
		}
		return errors.New("output already exists with different bytes")
	} else if !os.IsNotExist(err) {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".resource-write-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	_, writeErr := file.Write(raw)
	syncErr := file.Sync()
	if err := errors.Join(writeErr, syncErr, file.Close()); err != nil {
		return err
	}
	// Link installs the fully written file atomically without replacing another
	// writer's output. A process interruption can only leave an unused temp file.
	if err := os.Link(file.Name(), path); err != nil {
		if !os.IsExist(err) {
			return err
		}
		existing, readErr := readSmallRegular(path, int64(len(raw)))
		if readErr != nil || !bytes.Equal(existing, raw) {
			return errors.New("output already exists with different bytes")
		}
	}
	return nil
}

// PreparePublication validates the approval and emits a deterministic allowlist.
// The backend owns all remote writes, AList tasks, durable recovery and channel switching.
func PreparePublication(candidate, baseline, reviewPath, approvalSHA, output string) (PublicationPlan, error) {
	var plan PublicationPlan
	approvalRaw, err := readSmallRegular(reviewPath, 2<<20)
	if err != nil {
		return plan, err
	}
	if !validSHA(approvalSHA) || digest(approvalRaw) != approvalSHA {
		return plan, errors.New("approval review digest mismatch")
	}
	var approved Review
	if err := json.Unmarshal(approvalRaw, &approved); err != nil || approved.Schema != 2 {
		return plan, errors.New("resource approval schema 2 required; re-review legacy Git approvals")
	}
	if err := verifyReviewDetail(reviewPath, approved); err != nil {
		return plan, err
	}
	current, err := ComputeReview(candidate, baseline)
	if err != nil {
		return plan, err
	}
	left, _ := json.Marshal(approved)
	right, _ := json.Marshal(current)
	if !bytes.Equal(left, right) || len(current.Missing) > 0 {
		return plan, errors.New("candidate/baseline differs from approved review or has missing resources")
	}
	manifest, raw, err := manifestAt(filepath.Join(candidate, "manifest.json"))
	if err != nil {
		return plan, err
	}
	objects, err := manifestObjects(candidate, manifest)
	if err != nil {
		return plan, err
	}
	plan = PublicationPlan{Schema: 1, CandidateManifestSHA256: current.CandidateManifestSHA256, ReviewSHA256: approvalSHA, BaselineChannelSHA256: current.BaselineChannelSHA256, BaselineManifestSHA256: current.BaselineManifestSHA256, Objects: make([]PublicationFile, 0, len(objects))}
	for _, object := range objects {
		if !publicPath(object.Path) {
			return PublicationPlan{}, errors.New("non-public object in candidate")
		}
		plan.Objects = append(plan.Objects, PublicationFile{Path: object.Path, SHA256: object.SHA256, Bytes: object.Bytes})
	}
	plan.Release = PublicationFile{Path: "releases/" + plan.CandidateManifestSHA256 + ".json", SHA256: plan.CandidateManifestSHA256, Bytes: int64(len(raw))}
	channelRaw := pointerBytes(plan.CandidateManifestSHA256)
	plan.Channel = PublicationFile{Path: "channels/stable.json", SHA256: digest(channelRaw), Bytes: int64(len(channelRaw)), ManifestSHA256: plan.CandidateManifestSHA256}
	channelPath, err := safeRepoFile(candidate, plan.Channel.Path, true)
	if err != nil {
		return PublicationPlan{}, err
	}
	if err := writeExactFile(channelPath, channelRaw); err != nil {
		return PublicationPlan{}, err
	}
	planRaw, err := json.Marshal(plan)
	if err != nil {
		return PublicationPlan{}, err
	}
	return plan, writeExactFile(output, append(planRaw, '\n'))
}
