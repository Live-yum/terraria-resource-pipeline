// Package input reads uploaded archives with bounded buffers and private output.
package input

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

type Limits struct {
	ArchiveBytes, ExpandedBytes, FileBytes, DirectoryBytes int64
	Files                                                  int
}

func DefaultLimits() Limits {
	return Limits{2 << 30, 4 << 30, 1 << 30, 16 << 20, 50000}
}

type Entry struct {
	Path    string `json:"path"`
	Bytes   uint64 `json:"bytes"`
	SHA256  string `json:"sha256,omitempty"`
	Skipped string `json:"skipped,omitempty"`
}

type Inventory struct {
	ArchiveSHA256 string  `json:"archiveSha256"`
	ArchiveBytes  int64   `json:"archiveBytes"`
	ExpandedBytes uint64  `json:"expandedBytes"`
	Files         []Entry `json:"files"`
}

// RelativePath rejects platform aliases as well as traversal. Resource paths
// are shared with Windows checkouts and cannot depend on Linux normalization.
func RelativePath(name string) error {
	if name == "" || len(name) > 600 || !utf8.ValidString(name) || strings.ContainsAny(name, "\\:\x00") || path.IsAbs(name) {
		return errors.New("unsafe archive path")
	}
	for _, r := range name {
		if r < 32 {
			return errors.New("control character in archive path")
		}
	}
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." || len(part) > 200 || strings.TrimRight(part, " .") != part {
			return errors.New("nonportable archive path")
		}
		base := strings.ToLower(strings.SplitN(part, ".", 2)[0])
		if base == "con" || base == "prn" || base == "aux" || base == "nul" ||
			(len(base) == 4 && (strings.HasPrefix(base, "com") || strings.HasPrefix(base, "lpt")) && base[3] >= '1' && base[3] <= '9') {
			return errors.New("reserved archive path")
		}
	}
	return nil
}

// Bound the central directory before archive/zip allocates its file records.
// ZIP64 is accepted within the same upload, entry and directory limits.
func directoryLimits(file *os.File, size int64, limits Limits) error {
	length := min(size, int64(65535+22))
	if length < 22 {
		return errors.New("truncated ZIP")
	}
	tail := make([]byte, int(length))
	if _, err := file.ReadAt(tail, size-length); err != nil {
		return err
	}
	for i := len(tail) - 22; i >= 0; i-- {
		if binary.LittleEndian.Uint32(tail[i:]) != 0x06054b50 || i+22+int(binary.LittleEndian.Uint16(tail[i+20:])) != len(tail) {
			continue
		}
		if binary.LittleEndian.Uint16(tail[i+4:]) != 0 || binary.LittleEndian.Uint16(tail[i+6:]) != 0 {
			return errors.New("multidisk ZIP unsupported")
		}
		count := uint64(binary.LittleEndian.Uint16(tail[i+10:]))
		dirSize := uint64(binary.LittleEndian.Uint32(tail[i+12:]))
		dirOffset := uint64(binary.LittleEndian.Uint32(tail[i+16:]))
		if count == 65535 || dirSize == 0xffffffff || dirOffset == 0xffffffff {
			var locator [20]byte
			endOffset := size - length + int64(i)
			if endOffset < 20 {
				return errors.New("missing ZIP64 locator")
			}
			if _, err := file.ReadAt(locator[:], endOffset-20); err != nil {
				return err
			}
			if binary.LittleEndian.Uint32(locator[:]) != 0x07064b50 || binary.LittleEndian.Uint32(locator[4:]) != 0 || binary.LittleEndian.Uint32(locator[16:]) != 1 {
				return errors.New("invalid ZIP64 locator")
			}
			offset := binary.LittleEndian.Uint64(locator[8:])
			if offset > uint64(size)-56 {
				return errors.New("invalid ZIP64 offset")
			}
			var record [56]byte
			if _, err := file.ReadAt(record[:], int64(offset)); err != nil {
				return err
			}
			if binary.LittleEndian.Uint32(record[:]) != 0x06064b50 || binary.LittleEndian.Uint32(record[16:]) != 0 || binary.LittleEndian.Uint32(record[20:]) != 0 {
				return errors.New("invalid ZIP64 directory")
			}
			count = binary.LittleEndian.Uint64(record[32:])
			dirSize = binary.LittleEndian.Uint64(record[40:])
			dirOffset = binary.LittleEndian.Uint64(record[48:])
		}
		if count > uint64(limits.Files) || dirSize > uint64(limits.DirectoryBytes) || dirOffset > uint64(size) || dirSize > uint64(size)-dirOffset {
			return errors.New("ZIP directory exceeds limits")
		}
		return nil
	}
	return errors.New("ZIP directory not found")
}

// ExtractZIP publishes the directory only after every member has passed CRC,
// length and path checks. Existing output is never overwritten. Audio banks
// are recorded but not expanded: no current viewer consumer needs them.
func ExtractZIP(ctx context.Context, source, destination string, limits Limits) (result Inventory, err error) {
	if limits.ArchiveBytes <= 0 || limits.ExpandedBytes <= 0 || limits.FileBytes <= 0 || limits.DirectoryBytes <= 0 || limits.Files <= 0 {
		return result, errors.New("invalid archive limits")
	}
	file, err := os.Open(source)
	if err != nil {
		return result, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return result, err
	}
	if !info.Mode().IsRegular() || info.Size() > limits.ArchiveBytes {
		return result, errors.New("upload exceeds archive limit")
	}
	if err := directoryLimits(file, info.Size(), limits); err != nil {
		return result, err
	}
	reader, err := zip.NewReader(file, info.Size())
	if err != nil {
		return result, err
	}
	if len(reader.File) > limits.Files {
		return result, errors.New("too many ZIP entries")
	}
	seen := make(map[string]bool, len(reader.File))
	var declared uint64
	for _, member := range reader.File {
		name := strings.TrimSuffix(member.Name, "/")
		if err := RelativePath(name); err != nil {
			return result, fmt.Errorf("%s: %w", name, err)
		}
		key := strings.ToLower(name)
		if _, ok := seen[key]; ok {
			return result, errors.New("duplicate or case-colliding ZIP paths")
		}
		seen[key] = member.FileInfo().IsDir()
		if member.Mode()&os.ModeType != 0 && !member.FileInfo().IsDir() {
			return result, errors.New("ZIP links and special files forbidden")
		}
		if member.Flags&1 != 0 {
			return result, errors.New("encrypted ZIP unsupported")
		}
		if member.UncompressedSize64 > uint64(limits.FileBytes) || member.UncompressedSize64 > uint64(limits.ExpandedBytes)-declared {
			return result, errors.New("expanded ZIP exceeds limits")
		}
		declared += member.UncompressedSize64
	}
	for name := range seen {
		for parent := path.Dir(name); parent != "."; parent = path.Dir(parent) {
			if directory, ok := seen[parent]; ok && !directory {
				return result, errors.New("ZIP file conflicts with parent directory")
			}
		}
	}
	if _, err := os.Lstat(destination); !os.IsNotExist(err) {
		return result, errors.New("archive destination must be new")
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return result, err
	}
	temporary, err := os.MkdirTemp(filepath.Dir(destination), ".extract-")
	if err != nil {
		return result, err
	}
	defer os.RemoveAll(temporary)
	hash := sha256.New()
	buffer := make([]byte, 64<<10)
	if _, err := file.Seek(0, 0); err != nil {
		return result, err
	}
	if _, err := io.CopyBuffer(hash, file, buffer); err != nil {
		return result, err
	}
	result.ArchiveSHA256, result.ArchiveBytes = hex.EncodeToString(hash.Sum(nil)), info.Size()
	for _, member := range reader.File {
		if err := ctx.Err(); err != nil {
			return result, err
		}
		if member.FileInfo().IsDir() {
			continue
		}
		entry := Entry{Path: member.Name, Bytes: member.UncompressedSize64}
		if strings.EqualFold(path.Ext(member.Name), ".xwb") {
			entry.Skipped = "audio-bank-not-consumed"
			result.Files = append(result.Files, entry)
			continue
		}
		target := filepath.Join(temporary, filepath.FromSlash(member.Name))
		if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
			return result, err
		}
		input, err := member.Open()
		if err != nil {
			return result, err
		}
		output, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			input.Close()
			return result, err
		}
		hash.Reset()
		written, copyErr := io.CopyBuffer(io.MultiWriter(output, hash), io.LimitReader(input, int64(member.UncompressedSize64)+1), buffer)
		inputErr, outputErr := input.Close(), output.Close()
		if copyErr != nil {
			return result, copyErr
		}
		if inputErr != nil {
			return result, inputErr
		}
		if outputErr != nil {
			return result, outputErr
		}
		if uint64(written) != member.UncompressedSize64 {
			return result, errors.New("ZIP length mismatch")
		}
		result.ExpandedBytes += uint64(written)
		entry.SHA256 = hex.EncodeToString(hash.Sum(nil))
		result.Files = append(result.Files, entry)
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	// CLI callers own this private job directory; a competing creator still
	// cannot replace a populated destination through rename.
	if err := os.Rename(temporary, destination); err != nil {
		return result, err
	}
	return result, nil
}
