package pipeline

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"terraria-resource-pipeline/internal/input"
)

type RuntimeFamily struct {
	Path   string `json:"path"`
	Count  int    `json:"count"`
	SHA256 string `json:"sha256"`
}

type RuntimeResult struct {
	Protocol       int                      `json:"protocol"`
	GameVersion    string                   `json:"gameVersion"`
	AssemblySHA256 string                   `json:"assemblySha256"`
	Families       map[string]RuntimeFamily `json:"families"`
	PublicTextures RuntimeFamily            `json:"publicTextures"`
	Capabilities   map[string]any           `json:"capabilities"`
	Missing        []string                 `json:"missing"`
	Errors         []string                 `json:"errors"`
	Domains        map[string]int           `json:"idDomains,omitempty"`
	MapLayout      map[string]int           `json:"mapLayout,omitempty"`
	Metrics        map[string]any           `json:"metrics,omitempty"`
}

// These families are the client resource contract, not a game-version whitelist.
// A helper must not silently skip a renamed/missing ID domain after an update.
func validateRuntimeCoverage(result RuntimeResult) error {
	for _, name := range []string{
		"ids", "localization", "items", "item-field-schema", "item-tooltips", "item-ui-tooltips", "research",
		"tiles", "walls", "map", "map-palette", "map-lookup", "paints", "pixel-candidates",
		"tile-sets", "wall-sets", "tile-object-data", "mount-layouts", "armor-sets",
		"prefixes", "buffs", "bestiary", "npc-frames", "dye-shaders", "player-layouts",
		"texture-references", "player-texture-bindings", "player-draw-plans",
	} {
		if family, ok := result.Families[name]; !ok || family.Count <= 0 {
			return fmt.Errorf("required game resource family missing or empty: %s", name)
		}
	}
	if result.PublicTextures.Count <= 0 || result.PublicTextures.Path == "" ||
		!publicTextureClosureAvailable(result.Capabilities) {
		return errors.New("required public texture closure is missing or unavailable")
	}
	if _, exists := result.Families["publicTextures"]; exists {
		return errors.New("public texture selection must not be a published family")
	}
	var mounts struct {
		Available              bool `json:"available"`
		TextureDimensionsBound bool `json:"textureDimensionsBound"`
		EntityCount            int  `json:"entityCount"`
		Populated              int  `json:"populated"`
	}
	raw, err := json.Marshal(result.Capabilities["mountLayouts"])
	if err != nil || json.Unmarshal(raw, &mounts) != nil || !mounts.Available || !mounts.TextureDimensionsBound ||
		mounts.EntityCount <= 0 || mounts.Populated != mounts.EntityCount ||
		result.Families["mount-layouts"].Count != mounts.EntityCount || result.Domains["MountID"] != mounts.EntityCount {
		return errors.New("required official mount texture bindings or complete mount domain are unavailable")
	}
	return nil
}

type diagnosticBuffer struct{ text []byte }

func (b *diagnosticBuffer) Write(p []byte) (int, error) {
	if len(b.text) < 32768 {
		b.text = append(b.text, p[:min(len(p), 32768-len(b.text))]...)
	}
	return len(p), nil
}

// The Docker image is deployment configuration, never an uploaded value.
// The helper sees no Git keys, host home, network or Docker socket.
func RunRuntime(ctx context.Context, image, inputRoot, server, output, dimensions string) (RuntimeResult, error) {
	var result RuntimeResult
	helper := os.Getenv("TRP_RUNTIME_HELPER")
	if helper == "" && (image == "" || strings.HasPrefix(image, "-") || strings.ContainsAny(image, "\r\n\x00")) {
		return result, errors.New("invalid runtime image")
	}
	for _, name := range []string{inputRoot, output} {
		if strings.ContainsAny(name, ",\r\n") {
			return result, errors.New("runtime mount path contains unsupported punctuation")
		}
	}
	if err := os.Mkdir(output, 0700); err != nil {
		return result, err
	}
	var command *exec.Cmd
	if helper != "" {
		if !filepath.IsAbs(helper) {
			return result, errors.New("runtime helper must be an absolute deployment path")
		}
		command = exec.CommandContext(ctx, "mono", helper, "--server", server, "--output", output)
		command.Env = append(os.Environ(), "TRP_TEXTURE_DIMENSIONS="+dimensions)
	} else {
		// The containing job stays private (0700). Only this isolated output mount
		// is writable by the container's unprivileged UID on Linux hosts.
		if err := os.Chmod(output, 0777); err != nil {
			return result, err
		}
		if err := os.Chmod(dimensions, 0444); err != nil {
			return result, err
		}
		err := filepath.WalkDir(inputRoot, func(name string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.Type()&os.ModeSymlink != 0 {
				return errors.New("input contains symlink")
			}
			if entry.IsDir() {
				return os.Chmod(name, 0755)
			}
			return os.Chmod(name, 0444)
		})
		if err != nil {
			return result, err
		}
		relative, err := filepath.Rel(inputRoot, server)
		if err != nil {
			return result, err
		}
		if err := input.RelativePath(filepath.ToSlash(relative)); err != nil {
			return result, err
		}
		cidfile := output + ".cid"
		defer func() {
			if data, err := os.ReadFile(cidfile); err == nil {
				identity := strings.TrimSpace(string(data))
				if len(identity) == 64 && strings.Trim(identity, "0123456789abcdef") == "" {
					cleanup, cancel := context.WithTimeout(context.Background(), 15*time.Second)
					defer cancel()
					exec.CommandContext(cleanup, "docker", "rm", "--force", identity).Run()
				}
			}
			os.Remove(cidfile)
		}()
		arguments := []string{"run", "--rm", "--cidfile", cidfile, "--network", "none", "--read-only", "--cap-drop", "ALL",
			"--security-opt", "no-new-privileges", "--pids-limit", "64", "--cpus", "2", "--memory", "256m", "--memory-swap", "256m", "--user", "10001:10001",
			"--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "--env", "HOME=/tmp", "--env", "LANG=C.UTF-8",
			"--mount", "type=bind,src=" + inputRoot + ",dst=/input,readonly",
			"--mount", "type=bind,src=" + dimensions + ",dst=/dimensions.ndjson,readonly", "--env", "TRP_TEXTURE_DIMENSIONS=/dimensions.ndjson",
			"--mount", "type=bind,src=" + output + ",dst=/output", image,
			"--server", "/input/" + filepath.ToSlash(relative), "--output", "/output"}
		command = exec.CommandContext(ctx, "docker", arguments...)
	}
	var diagnostic diagnosticBuffer
	command.Stdout, command.Stderr = &diagnostic, &diagnostic
	if err := command.Run(); err != nil {
		return result, fmt.Errorf("game runtime failed: %w; %s", err, string(diagnostic.text))
	}
	if err := os.Chmod(output, 0700); err != nil {
		return result, err
	}
	resultPath := filepath.Join(output, "result.json")
	info, err := os.Lstat(resultPath)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 1<<20 {
		return result, errors.New("runtime result missing, unsafe or exceeds budget")
	}
	bytes, err := os.ReadFile(resultPath)
	if err != nil {
		return result, fmt.Errorf("runtime result missing: %w", err)
	}
	if len(bytes) > 1<<20 {
		return result, errors.New("runtime result exceeds budget")
	}
	if err := json.Unmarshal(bytes, &result); err != nil {
		return result, err
	}
	digest, err := input.FileSHA256(server)
	if err != nil {
		return result, err
	}
	if result.Protocol != 1 || result.GameVersion == "" || result.AssemblySHA256 != digest || len(result.Families) == 0 {
		return result, errors.New("runtime result does not match input or protocol")
	}
	// An incomplete helper result still needs to reach Extract's error report;
	// it need not claim a successful public texture selection.
	if len(result.Errors) != 0 {
		return result, nil
	}
	for name, family := range result.Families {
		if _, err := validatedRuntimeFile(output, family, 256<<20, name); err != nil {
			return result, err
		}
	}
	if _, err := validatedRuntimeFile(output, result.PublicTextures, 16<<20, "publicTextures"); err != nil {
		return result, err
	}
	return result, nil
}

func validatedRuntimeFile(root string, descriptor RuntimeFamily, maxBytes int64, name string) (string, error) {
	if err := input.RelativePath(descriptor.Path); err != nil {
		return "", fmt.Errorf("unsafe runtime path %s: %w", name, err)
	}
	if descriptor.Count < 0 || descriptor.Count > 100000 || len(descriptor.SHA256) != 64 {
		return "", fmt.Errorf("invalid runtime descriptor %s", name)
	}
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("unsafe runtime root for %s", name)
	}
	path := root
	for _, component := range strings.Split(descriptor.Path, "/") {
		path = filepath.Join(path, component)
		info, err = os.Lstat(path)
		if err != nil || info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("unsafe runtime path %s", name)
		}
	}
	if !info.Mode().IsRegular() || info.Size() > maxBytes || (name == "publicTextures" && info.Size() == 0) {
		return "", fmt.Errorf("invalid runtime file %s", name)
	}
	actual, err := input.FileSHA256(path)
	if err != nil {
		return "", err
	}
	if actual != descriptor.SHA256 {
		return "", fmt.Errorf("runtime hash mismatch: %s", name)
	}
	return path, nil
}
