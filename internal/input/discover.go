package input

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type Sources struct {
	Server            string `json:"-"`
	Content           string `json:"-"`
	ServerSHA256      string `json:"serverSha256"`
	ContentTreeSHA256 string `json:"contentTreeSha256"`
	TextureFiles      int    `json:"textureFiles"`
}

// Discover accepts an optional outer ZIP folder and prefers the dedicated
// Linux assembly when an official archive includes all three platforms.
// Ambiguous versions or Content roots must be resolved by the uploader.
func Discover(root string) (Sources, error) {
	var result Sources
	var servers, linuxServers, contents []string
	err := filepath.WalkDir(root, func(name string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("source contains a link")
		}
		if entry.IsDir() {
			if strings.EqualFold(entry.Name(), "Content") {
				if stat, err := os.Stat(filepath.Join(name, "Images")); err == nil && stat.IsDir() {
					contents = append(contents, name)
				}
			}
			return nil
		}
		if !strings.EqualFold(entry.Name(), "TerrariaServer.exe") {
			return nil
		}
		servers = append(servers, name)
		for _, part := range strings.Split(filepath.ToSlash(name), "/") {
			if strings.EqualFold(part, "Linux") {
				linuxServers = append(linuxServers, name)
				break
			}
		}
		return nil
	})
	if err != nil {
		return result, err
	}
	if len(linuxServers) == 1 {
		result.Server = linuxServers[0]
	} else if len(linuxServers) == 0 && len(servers) == 1 {
		result.Server = servers[0]
	} else {
		return result, errors.New("need one server version; include its Linux distribution when available")
	}
	if len(contents) != 1 {
		return result, errors.New("need one Content directory containing Images")
	}
	result.Content = contents[0]
	if result.ServerSHA256, err = FileSHA256(result.Server); err != nil {
		return result, err
	}
	result.ContentTreeSHA256, result.TextureFiles, err = ContentDigest(result.Content)
	return result, err
}

func FileSHA256(name string) (string, error) {
	file, err := os.Open(name)
	if err != nil {
		return "", err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() {
		return "", errors.New("expected a regular file")
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

// Only consumed texture input contributes to the resource tree identity.
// Archive identity separately includes unused audio, packaging and timestamps.
func ContentDigest(root string) (string, int, error) {
	var names []string
	err := filepath.WalkDir(filepath.Join(root, "Images"), func(name string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("Content contains a link")
		}
		if !entry.IsDir() && (strings.EqualFold(filepath.Ext(name), ".xnb") || strings.EqualFold(filepath.Ext(name), ".png")) {
			names = append(names, name)
		}
		return nil
	})
	if err != nil {
		return "", 0, err
	}
	if len(names) == 0 {
		return "", 0, errors.New("Content has no texture inputs")
	}
	sort.Strings(names)
	hash := sha256.New()
	encoder := json.NewEncoder(hash)
	for _, name := range names {
		digest, err := FileSHA256(name)
		if err != nil {
			return "", 0, err
		}
		info, err := os.Stat(name)
		if err != nil {
			return "", 0, err
		}
		relative, err := filepath.Rel(root, name)
		if err != nil {
			return "", 0, err
		}
		row := struct {
			Path   string `json:"path"`
			Bytes  int64  `json:"bytes"`
			SHA256 string `json:"sha256"`
		}{filepath.ToSlash(relative), info.Size(), digest}
		if err := encoder.Encode(row); err != nil {
			return "", 0, fmt.Errorf("texture inventory: %w", err)
		}
	}
	return hex.EncodeToString(hash.Sum(nil)), len(names), nil
}
