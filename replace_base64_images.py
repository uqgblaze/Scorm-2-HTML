#!/usr/bin/env python3
"""Replace embedded base64 images in an HTML file with a hosted placeholder image.

Usage:
  python replace_base64_images.py input.html output.html
  python replace_base64_images.py input.html output.html --url 'https://placehold.co/1200?text=Placeholder+Image&font=roboto'
"""
import argparse
import re
from pathlib import Path

IMAGE_DATA_URL = re.compile(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+', re.IGNORECASE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Input HTML file')
    parser.add_argument('output', type=Path, help='Output HTML file')
    parser.add_argument('--url', default='https://placehold.co/1200?text=Placeholder+Image&font=roboto',
                        help='Hosted placeholder image URL')
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Input and output paths must differ to protect the original file')
    html = args.input.read_text(encoding='utf-8')
    replaced, count = IMAGE_DATA_URL.subn(lambda _: args.url, html)
    args.output.write_text(replaced, encoding='utf-8')
    print(f'Replaced {count} embedded image(s).')
    print(f'{args.input.stat().st_size:,} bytes -> {args.output.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
