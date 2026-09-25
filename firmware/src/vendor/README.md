# QR Code generator

qrcodegen.c and qrcodegen.h are unmodified Nayuki C QR Code generator sources.
Upstream: https://github.com/nayuki/QR-Code-generator
Revision: 3c6d0b3cefb4e049dc337e82237c9644399716a8
License: MIT (copyright and permission notice retained in both files).

The setup screen encodes the generated open AP name using the ZXing Wi-Fi
format: https://github.com/zxing/zxing/wiki/Barcode-Contents#wi-fi-network-config-android-ios-11
Version 3 is rendered at 3 pixels per module with a four-module white quiet zone.
The existing captive DNS and HTTP redirect handle the setup page after joining.
Android may require tapping its sign-in notification; the on-screen IP remains
available for manual access.

Verification: run the native screen tests, then run
`python firmware/test/check_setup_qr.py` with Pillow and zxing-cpp installed.
This independently decodes the rendered framebuffer at four rotations.
Physical camera scanning and captive-portal launch require an Android phone.
