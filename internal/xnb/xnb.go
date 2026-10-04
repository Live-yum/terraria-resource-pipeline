// Package xnb decodes XNB v5 Texture2D data without loading XNB reader types.
package xnb

import (
	"encoding/binary"
	"errors"
	"fmt"
	"image"
	"io"
	"math/bits"
	"strings"
	"unicode/utf8"
)

var ErrNotTexture = errors.New("xnb: asset is not Texture2D")

const maxPixels = 16_000_000
const maxPayload = 128 << 20

// Decode reads at most maxBytes of one XNB file. maxBytes must be positive.
// The returned NRGBA preserves the unassociated RGBA bytes in the asset.
func Decode(r io.Reader, maxBytes int64) (*image.NRGBA, error) {
	if maxBytes < 10 || maxBytes > maxPayload {
		return nil, fmt.Errorf("xnb: invalid input limit %d", maxBytes)
	}
	src, err := io.ReadAll(io.LimitReader(r, maxBytes+1))
	if err != nil {
		return nil, fmt.Errorf("xnb: read: %w", err)
	}
	if int64(len(src)) > maxBytes {
		return nil, errors.New("xnb: input exceeds limit")
	}
	if len(src) < 10 || string(src[:3]) != "XNB" || (src[3] != 'w' && src[3] != 'm' && src[3] != 'x') || src[4] != 5 || src[5]&^byte(0x81) != 0 || uint64(binary.LittleEndian.Uint32(src[6:10])) != uint64(len(src)) {
		return nil, errors.New("xnb: invalid header")
	}
	payload := src[10:]
	if src[5]&0x80 != 0 {
		if len(payload) < 4 {
			return nil, errors.New("xnb: missing uncompressed size")
		}
		size := int(binary.LittleEndian.Uint32(payload[:4]))
		if size < 1 || size > maxPayload {
			return nil, errors.New("xnb: invalid uncompressed size")
		}
		payload, err = decompressLZX(payload[4:], size)
		if err != nil {
			return nil, fmt.Errorf("xnb: LZX: %w", err)
		}
	}
	p := parser{data: payload}
	count, err := p.varint()
	if err != nil || count < 1 || count > 64 {
		return nil, errors.New("xnb: invalid reader count")
	}
	readers := make([]string, count)
	for i := range readers {
		name, err := p.string()
		if err != nil {
			return nil, err
		}
		readers[i] = strings.SplitN(name, ",", 2)[0]
		if _, err := p.take(4); err != nil {
			return nil, err
		} // reader version
	}
	shared, err := p.varint()
	if err != nil || shared != 0 {
		return nil, errors.New("xnb: shared resources unsupported")
	}
	primary, err := p.varint()
	if err != nil || primary < 1 || primary > count {
		return nil, errors.New("xnb: invalid primary reader")
	}
	if readers[primary-1] != "Microsoft.Xna.Framework.Content.Texture2DReader" {
		return nil, ErrNotTexture
	}
	format, err := p.uint32()
	if err != nil {
		return nil, err
	}
	w, err := p.uint32()
	if err != nil {
		return nil, err
	}
	h, err := p.uint32()
	if err != nil {
		return nil, err
	}
	mips, err := p.uint32()
	if err != nil {
		return nil, err
	}
	if w == 0 || h == 0 || uint64(w)*uint64(h) > maxPixels || mips == 0 || mips > uint32(1+bits.Len32(max(w, h)-1)) {
		return nil, errors.New("xnb: invalid dimensions or mip count")
	}
	if format != 0 && format != 4 && format != 5 && format != 6 {
		return nil, errors.New("xnb: unsupported texture format")
	}
	var pix []byte
	for mip := uint32(0); mip < mips; mip++ {
		mw, mh := max(1, w>>mip), max(1, h>>mip)
		expected := uint64(mw) * uint64(mh) * 4
		if format != 0 {
			blockBytes := uint64(16)
			if format == 4 {
				blockBytes = 8
			}
			expected = uint64((mw+3)/4) * uint64((mh+3)/4) * blockBytes
		}
		n, err := p.uint32()
		if err != nil || uint64(n) != expected {
			return nil, errors.New("xnb: invalid mip size")
		}
		data, err := p.take(int(n))
		if err != nil {
			return nil, err
		}
		if mip == 0 {
			if format == 0 {
				pix = data
			} else {
				pix = decodeDXT(data, int(w), int(h), int(format))
			}
		}
	}
	if p.pos != len(p.data) {
		return nil, errors.New("xnb: trailing payload")
	}
	return &image.NRGBA{Pix: pix, Stride: int(w) * 4, Rect: image.Rect(0, 0, int(w), int(h))}, nil
}

type parser struct {
	data []byte
	pos  int
}

func (p *parser) take(n int) ([]byte, error) {
	if n < 0 || n > len(p.data)-p.pos {
		return nil, io.ErrUnexpectedEOF
	}
	b := p.data[p.pos : p.pos+n]
	p.pos += n
	return b, nil
}
func (p *parser) uint32() (uint32, error) {
	b, e := p.take(4)
	if e != nil {
		return 0, e
	}
	return binary.LittleEndian.Uint32(b), nil
}
func (p *parser) varint() (int, error) {
	v := uint32(0)
	for i := 0; i < 5; i++ {
		b, e := p.take(1)
		if e != nil {
			return 0, e
		}
		if i == 4 && b[0] > 7 {
			return 0, errors.New("xnb: varint overflow")
		}
		v |= uint32(b[0]&127) << uint(i*7)
		if b[0] < 128 {
			return int(v), nil
		}
	}
	return 0, errors.New("xnb: unterminated varint")
}
func (p *parser) string() (string, error) {
	n, e := p.varint()
	if e != nil {
		return "", e
	}
	if n > 4096 {
		return "", errors.New("xnb: reader name too long")
	}
	b, e := p.take(n)
	if e != nil {
		return "", e
	}
	if !utf8.Valid(b) {
		return "", errors.New("xnb: invalid reader name")
	}
	return string(b), nil
}
