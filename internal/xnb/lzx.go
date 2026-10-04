package xnb

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// This is a bounded Go adaptation of TConvert's MIT-licensed LZX decoder.
// XNB uses 32 KiB frames and a persistent 64 KiB LZX dictionary.
func decompressLZX(src []byte, size int) ([]byte, error) {
	out := make([]byte, size)
	d := newLZX()
	pos, done := 0, 0
	for pos < len(src) {
		if done == size {
			// XNB's LZX stream terminates with five zero bytes in Terraria assets.
			if len(src)-pos > 5 {
				return nil, errors.New("unexpected LZX trailer")
			}
			for _, v := range src[pos:] {
				if v != 0 {
					return nil, errors.New("nonzero LZX trailer")
				}
			}
			break
		}
		if len(src)-pos < 2 {
			return nil, io.ErrUnexpectedEOF
		}
		a, b := int(src[pos]), int(src[pos+1])
		pos += 2
		frameSize, blockSize := 32768, (a<<8)|b
		if a == 255 {
			if len(src)-pos < 3 {
				return nil, io.ErrUnexpectedEOF
			}
			frameSize = (b << 8) | int(src[pos])
			blockSize = int(src[pos+1])<<8 | int(src[pos+2])
			pos += 3
		}
		if frameSize < 1 || frameSize > 32768 || blockSize < 1 || blockSize > len(src)-pos || frameSize > size-done {
			return nil, errors.New("invalid LZX frame")
		}
		if err := d.frame(src[pos:pos+blockSize], out[done:done+frameSize]); err != nil {
			return nil, fmt.Errorf("frame %d: %w", done/32768, err)
		}
		pos += blockSize
		done += frameSize
	}
	if done != size {
		return nil, errors.New("LZX output size mismatch")
	}
	return out, nil
}

type bitstream struct {
	data  []byte
	pos   int
	bits  uint32
	nbits uint
}

func (b *bitstream) read(n uint) (int, error) {
	if n == 0 {
		return 0, nil
	}
	if n > 16 {
		return 0, errors.New("LZX bit count")
	}
	for b.nbits < n {
		if len(b.data)-b.pos < 2 {
			return 0, io.ErrUnexpectedEOF
		}
		word := uint32(binary.LittleEndian.Uint16(b.data[b.pos:]))
		b.pos += 2
		b.bits |= word << (16 - b.nbits)
		b.nbits += 16
	}
	v := int(b.bits >> (32 - n))
	b.bits <<= n
	b.nbits -= n
	return v, nil
}
func (b *bitstream) uncompressed() error {
	for b.nbits < 16 {
		if len(b.data)-b.pos < 2 {
			return io.ErrUnexpectedEOF
		}
		word := uint32(binary.LittleEndian.Uint16(b.data[b.pos:]))
		b.pos += 2
		b.bits |= word << (16 - b.nbits)
		b.nbits += 16
	}
	if b.nbits > 16 {
		b.pos -= 2
	}
	b.bits = 0
	b.nbits = 0
	return nil
}
func (b *bitstream) bytes(n int) ([]byte, error) {
	if n < 0 || n > len(b.data)-b.pos {
		return nil, io.ErrUnexpectedEOF
	}
	v := b.data[b.pos : b.pos+n]
	b.pos += n
	return v, nil
}

type huffman struct {
	lens    []byte
	counts  [17]int
	first   [17]int
	start   [17]int
	symbols []int
}

func newHuffman(n int) huffman { return huffman{lens: make([]byte, n)} }
func (h *huffman) build() error {
	h.counts = [17]int{}
	h.first = [17]int{}
	h.start = [17]int{}
	weight := 0
	for _, l := range h.lens {
		if l > 16 {
			return errors.New("LZX Huffman length")
		}
		if l > 0 {
			h.counts[l]++
			weight += 1 << (16 - l)
		}
	}
	if weight != 0 && weight != 65536 {
		return errors.New("incomplete LZX Huffman tree")
	}
	code, total := 0, 0
	for n := 1; n <= 16; n++ {
		code = (code + h.counts[n-1]) << 1
		h.first[n] = code
		h.start[n] = total
		total += h.counts[n]
	}
	h.symbols = make([]int, total)
	var index [17]int
	for symbol, l := range h.lens {
		if l > 0 {
			n := int(l)
			h.symbols[h.start[n]+index[n]] = symbol
			index[n]++
		}
	}
	return nil
}
func (h *huffman) read(b *bitstream) (int, error) {
	code := 0
	for n := 1; n <= 16; n++ {
		bit, err := b.read(1)
		if err != nil {
			return 0, err
		}
		code = (code << 1) | bit
		delta := code - h.first[n]
		if delta >= 0 && delta < h.counts[n] {
			return h.symbols[h.start[n]+delta], nil
		}
	}
	return 0, errors.New("invalid LZX Huffman code")
}

type lzx struct {
	window                            [65536]byte
	windowPos, history                int
	r0, r1, r2                        int
	blockType, blockLength, remaining int
	readHeader                        bool
	pre, main, length, aligned        huffman
	extra                             [52]int
	base                              [51]int
}

func newLZX() *lzx {
	d := &lzx{r0: 1, r1: 1, r2: 1, readHeader: true, pre: newHuffman(20), main: newHuffman(512), length: newHuffman(250), aligned: newHuffman(8)}
	j := 0
	for i := 0; i <= 50; i += 2 {
		d.extra[i] = j
		d.extra[i+1] = j
		if i != 0 && j < 17 {
			j++
		}
	}
	j = 0
	for i := 0; i <= 50; i++ {
		d.base[i] = j
		j += 1 << d.extra[i]
	}
	return d
}
func (d *lzx) put(dst []byte, at *int, v byte) {
	d.window[d.windowPos] = v
	d.windowPos = (d.windowPos + 1) & 65535
	if d.history < 65536 {
		d.history++
	}
	dst[*at] = v
	*at++
}
func (d *lzx) readLengths(h *huffman, first, last int, b *bitstream) error {
	for i := 0; i < 20; i++ {
		v, e := b.read(4)
		if e != nil {
			return e
		}
		d.pre.lens[i] = byte(v)
	}
	if e := d.pre.build(); e != nil {
		return e
	}
	for i := first; i < last; {
		z, e := d.pre.read(b)
		if e != nil {
			return e
		}
		switch z {
		case 17, 18:
			bits, add := uint(4), 4
			if z == 18 {
				bits, add = 5, 20
			}
			n, e := b.read(bits)
			if e != nil {
				return e
			}
			n += add
			if n > last-i {
				return errors.New("LZX length run overflow")
			}
			for j := 0; j < n; j++ {
				h.lens[i] = 0
				i++
			}
		case 19:
			n, e := b.read(1)
			if e != nil {
				return e
			}
			n += 4
			if n > last-i {
				return errors.New("LZX length run overflow")
			}
			z, e = d.pre.read(b)
			if e != nil {
				return e
			}
			v := (int(h.lens[i]) - z + 17) % 17
			for j := 0; j < n; j++ {
				h.lens[i] = byte(v)
				i++
			}
		default:
			h.lens[i] = byte((int(h.lens[i]) - z + 17) % 17)
			i++
		}
	}
	return nil
}
func (d *lzx) blockHeader(b *bitstream) error {
	if d.blockType == 3 && d.blockLength&1 == 1 {
		if _, e := b.bytes(1); e != nil {
			return e
		}
		b.bits = 0
		b.nbits = 0
	}
	t, e := b.read(3)
	if e != nil {
		return e
	}
	if t < 1 || t > 3 {
		return errors.New("invalid LZX block type")
	}
	a, e := b.read(16)
	if e != nil {
		return e
	}
	z, e := b.read(8)
	if e != nil {
		return e
	}
	d.blockType = t
	d.blockLength = a<<8 | z
	d.remaining = d.blockLength
	if d.remaining == 0 {
		return errors.New("empty LZX block")
	}
	switch t {
	case 1, 2:
		if t == 2 {
			for i := 0; i < 8; i++ {
				v, e := b.read(3)
				if e != nil {
					return e
				}
				d.aligned.lens[i] = byte(v)
			}
			if e := d.aligned.build(); e != nil {
				return e
			}
		}
		if e := d.readLengths(&d.main, 0, 256, b); e != nil {
			return e
		}
		if e := d.readLengths(&d.main, 256, 512, b); e != nil {
			return e
		}
		if e := d.main.build(); e != nil {
			return e
		}
		if e := d.readLengths(&d.length, 0, 249, b); e != nil {
			return e
		}
		if e := d.length.build(); e != nil {
			return e
		}
	case 3:
		if e := b.uncompressed(); e != nil {
			return e
		}
		v, e := b.bytes(12)
		if e != nil {
			return e
		}
		d.r0 = int(binary.LittleEndian.Uint32(v))
		d.r1 = int(binary.LittleEndian.Uint32(v[4:]))
		d.r2 = int(binary.LittleEndian.Uint32(v[8:]))
		if d.r0 < 1 || d.r0 > 65536 || d.r1 < 1 || d.r1 > 65536 || d.r2 < 1 || d.r2 > 65536 {
			return errors.New("invalid LZX offset")
		}
	}
	return nil
}
func (d *lzx) frame(src, dst []byte) error {
	b := bitstream{data: src}
	if d.readHeader {
		v, e := b.read(1)
		if e != nil {
			return e
		}
		if v != 0 {
			return errors.New("Intel E8 transform unsupported")
		}
		d.readHeader = false
	}
	at := 0
	for at < len(dst) {
		if d.remaining == 0 {
			if e := d.blockHeader(&b); e != nil {
				return e
			}
		}
		run := min(d.remaining, len(dst)-at)
		if d.blockType == 3 {
			v, e := b.bytes(run)
			if e != nil {
				return e
			}
			for _, c := range v {
				d.put(dst, &at, c)
			}
			d.remaining -= run
			continue
		}
		for left := run; left > 0; {
			sym, e := d.main.read(&b)
			if e != nil {
				return e
			}
			if sym < 256 {
				d.put(dst, &at, byte(sym))
				left--
				continue
			}
			sym -= 256
			length := sym & 7
			if length == 7 {
				v, e := d.length.read(&b)
				if e != nil {
					return e
				}
				length += v
			}
			length += 2
			if length > left {
				return errors.New("LZX match exceeds block/frame")
			}
			slot := sym >> 3
			var offset int
			switch slot {
			case 0:
				offset = d.r0
			case 1:
				offset = d.r1
				d.r1 = d.r0
				d.r0 = offset
			case 2:
				offset = d.r2
				d.r2 = d.r0
				d.r0 = offset
			default:
				if slot >= len(d.base) {
					return errors.New("LZX offset slot")
				}
				if slot == 3 {
					offset = 1
				} else {
					extra := d.extra[slot]
					offset = d.base[slot] - 2
					if d.blockType == 1 {
						v, e := b.read(uint(extra))
						if e != nil {
							return e
						}
						offset += v
					} else if extra > 3 {
						v, e := b.read(uint(extra - 3))
						if e != nil {
							return e
						}
						a, e := d.aligned.read(&b)
						if e != nil {
							return e
						}
						offset += (v << 3) + a
					} else if extra == 3 {
						a, e := d.aligned.read(&b)
						if e != nil {
							return e
						}
						offset += a
					} else if extra > 0 {
						v, e := b.read(uint(extra))
						if e != nil {
							return e
						}
						offset += v
					} else {
						offset = 1
					}
				}
				d.r2 = d.r1
				d.r1 = d.r0
				d.r0 = offset
			}
			if offset < 1 || offset > d.history {
				return errors.New("LZX match outside history")
			}
			for i := 0; i < length; i++ {
				d.put(dst, &at, d.window[(d.windowPos-offset+65536)&65535])
			}
			left -= length
		}
		d.remaining -= run
	}
	if b.pos > len(src) {
		return io.ErrUnexpectedEOF
	}
	return nil
}
