package pipeline

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"

	"terraria-resource-pipeline/internal/artifact"
)

func decodeRow(raw []byte) (string, json.RawMessage, map[string]any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var row map[string]any
	if err := decoder.Decode(&row); err != nil {
		return "", nil, nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return "", nil, nil, errors.New("metadata row contains trailing JSON")
	}
	id, ok := row["id"]
	if !ok {
		return "", nil, nil, errors.New("metadata row has no identity")
	}
	identity, err := json.Marshal(id)
	if err != nil {
		return "", nil, nil, err
	}
	return string(identity), identity, row, nil
}

func ReadObject(root string, object artifact.Object, destination any) error {
	if err := artifact.Verify(root, object); err != nil {
		return err
	}
	file, err := os.Open(filepath.Join(root, filepath.FromSlash(object.Path)))
	if err != nil {
		return err
	}
	defer file.Close()
	var reader io.Reader = file
	if object.Encoding == "gzip" {
		zipper, err := gzip.NewReader(file)
		if err != nil {
			return err
		}
		defer zipper.Close()
		reader = zipper
	}
	return json.NewDecoder(io.LimitReader(reader, artifact.MaxBytes)).Decode(destination)
}
