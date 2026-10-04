package artifact

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"terraria-resource-pipeline/internal/input"
)

const MaxBytes = 128 << 20

type Object struct {
	Path         string `json:"path"`
	SHA256       string `json:"sha256"`
	Bytes        int64  `json:"bytes"`
	Encoding     string `json:"encoding,omitempty"`
	DecodedBytes int64  `json:"decodedBytes,omitempty"`
	MediaType    string `json:"mediaType"`
}

type Store struct{ Root string }

type countedWriter struct {
	writer io.Writer
	count  int64
}

func (w *countedWriter) Write(data []byte) (int, error) {
	if int64(len(data)) > MaxBytes-w.count {
		return 0, errors.New("resource exceeds object budget")
	}
	n, err := w.writer.Write(data)
	w.count += int64(n)
	return n, err
}

func New(root string) (*Store, error) {
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Join(abs, "objects"), 0700); err != nil {
		return nil, err
	}
	return &Store{Root: abs}, nil
}

func (s *Store) Put(extension, mediaType string, write func(io.Writer) error) (Object, error) {
	var object Object
	switch extension {
	case ".json.gz", ".png", ".zip", ".zip.gz", ".txci.gz", ".srgb.gz", ".bin.gz", ".json", ".bin":
	default:
		return object, errors.New("unsupported object extension")
	}
	temporary, err := os.CreateTemp(s.Root, ".object-")
	if err != nil {
		return object, err
	}
	defer os.Remove(temporary.Name())
	hash := sha256.New()
	w := &countedWriter{writer: io.MultiWriter(temporary, hash)}
	if err := write(w); err != nil {
		temporary.Close()
		return object, err
	}
	// These are reproducible attempt outputs. Verify every referenced object
	// before publishing; an interrupted attempt is never resumed or published.
	if err := temporary.Close(); err != nil {
		return object, err
	}
	digest := hex.EncodeToString(hash.Sum(nil))
	object = Object{Path: "objects/" + digest[:2] + "/" + digest + extension, SHA256: digest, Bytes: w.count, MediaType: mediaType}
	target := filepath.Join(s.Root, filepath.FromSlash(object.Path))
	if _, err := os.Lstat(target); err == nil {
		if err := Verify(s.Root, object); err != nil {
			return Object{}, err
		}
		return object, nil
	} else if !os.IsNotExist(err) {
		return Object{}, err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
		return Object{}, err
	}
	if err := os.Rename(temporary.Name(), target); err != nil {
		return Object{}, err
	}
	return object, nil
}

func (s *Store) Gzip(extension, mediaType string, write func(io.Writer) error) (Object, error) {
	var decoded int64
	object, err := s.Put(extension, mediaType, func(destination io.Writer) error {
		zipper, err := gzip.NewWriterLevel(destination, gzip.BestCompression)
		if err != nil {
			return err
		}
		writer := &countedWriter{writer: zipper}
		writeErr := write(writer)
		closeErr := zipper.Close()
		decoded = writer.count
		if writeErr != nil {
			return writeErr
		}
		return closeErr
	})
	if err != nil {
		return Object{}, err
	}
	object.Encoding, object.DecodedBytes = "gzip", decoded
	return object, nil
}

func (s *Store) JSON(value any) (Object, error) {
	return s.Gzip(".json.gz", "application/json", func(writer io.Writer) error { return json.NewEncoder(writer).Encode(value) })
}

func safeFile(root, relative string) (string, error) {
	if err := input.RelativePath(relative); err != nil {
		return "", err
	}
	if !strings.HasPrefix(relative, "objects/") {
		return "", errors.New("resource outside object directory")
	}
	current := root
	for _, component := range strings.Split(relative, "/") {
		current = filepath.Join(current, component)
		info, err := os.Lstat(current)
		if err != nil {
			return "", err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return "", errors.New("resource path contains symlink")
		}
	}
	return current, nil
}

func Verify(root string, object Object) error {
	if object.Bytes < 0 || object.Bytes > MaxBytes || len(object.SHA256) != 64 {
		return errors.New("invalid resource descriptor")
	}
	if _, err := hex.DecodeString(object.SHA256); err != nil {
		return errors.New("invalid resource hash")
	}
	if !strings.HasPrefix(object.Path, "objects/"+object.SHA256[:2]+"/"+object.SHA256+".") {
		return errors.New("resource path does not match hash")
	}
	filename, err := safeFile(root, object.Path)
	if err != nil {
		return err
	}
	file, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() != object.Bytes {
		return errors.New("resource size mismatch")
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return err
	}
	if hex.EncodeToString(hash.Sum(nil)) != object.SHA256 {
		return fmt.Errorf("resource hash mismatch: %s", object.Path)
	}
	if object.Encoding == "gzip" {
		if object.DecodedBytes < 0 || object.DecodedBytes > MaxBytes {
			return errors.New("resource decoded size exceeds budget")
		}
		if _, err := file.Seek(0, 0); err != nil {
			return err
		}
		zipper, err := gzip.NewReader(file)
		if err != nil {
			return err
		}
		defer zipper.Close()
		count, err := io.Copy(io.Discard, io.LimitReader(zipper, object.DecodedBytes+1))
		if err != nil {
			return err
		}
		if count != object.DecodedBytes {
			return errors.New("resource decoded size mismatch")
		}
	} else if object.Encoding != "" {
		return errors.New("unsupported resource encoding")
	}
	return nil
}
