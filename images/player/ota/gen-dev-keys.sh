#!/bin/bash
# Create a local DEV/TEST OTA PKI for FreeSounder (never for releases):
#   ca.key.pem / ca.cert.pem            dev CA; ca.cert.pem is the image keyring (RAUC_KEYRING_PEM)
#   signing.key.pem / signing.cert.pem  bundle signer issued by the CA (codeSigning)
# Default location: ~/.config/freesounder-build/ota-dev-keys (mode 0700, keys 0600).
# Private keys are never printed. Release keys live only in GitHub Actions secrets.
set -euo pipefail
OUT=${1:-${OTA_DEV_KEYS:-$HOME/.config/freesounder-build/ota-dev-keys}}
DAYS=${DAYS:-3650}
umask 077
mkdir -p "$OUT"
chmod 700 "$OUT"
if [[ -f "$OUT/ca.key.pem" && -f "$OUT/signing.key.pem" ]]; then
	echo "Dev keys already exist in $OUT (delete them to regenerate)."
	exit 0
fi
cnf=$(mktemp)
trap 'rm -f "$cnf" "$OUT/signing.csr.pem"' EXIT
cat > "$cnf" <<'EOF'
[ca_ext]
basicConstraints=critical,CA:TRUE
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
[sign_ext]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
EOF
openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/ca.key.pem" 2>/dev/null
openssl req -new -x509 -key "$OUT/ca.key.pem" -days "$DAYS" -subj "/O=FreeSounder/CN=FreeSounder OTA DEV CA" \
	-extensions ca_ext -config <(cat /etc/ssl/openssl.cnf "$cnf") -out "$OUT/ca.cert.pem" 2>/dev/null
openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/signing.key.pem" 2>/dev/null
openssl req -new -key "$OUT/signing.key.pem" -subj "/O=FreeSounder/CN=FreeSounder OTA DEV signing" \
	-out "$OUT/signing.csr.pem" 2>/dev/null
openssl x509 -req -in "$OUT/signing.csr.pem" -CA "$OUT/ca.cert.pem" -CAkey "$OUT/ca.key.pem" -CAcreateserial \
	-days "$DAYS" -extfile "$cnf" -extensions sign_ext -out "$OUT/signing.cert.pem" 2>/dev/null
rm -f "$OUT/ca.cert.srl"
chmod 600 "$OUT"/*.key.pem
chmod 644 "$OUT"/*.cert.pem
openssl verify -purpose codesign -CAfile "$OUT/ca.cert.pem" "$OUT/signing.cert.pem" >/dev/null 2>&1 \
	|| openssl verify -CAfile "$OUT/ca.cert.pem" "$OUT/signing.cert.pem" >/dev/null
echo "Dev OTA keys written to $OUT (private keys not shown)."
echo "  keyring (public): $OUT/ca.cert.pem"
