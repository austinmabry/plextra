#!/usr/bin/env bash
# Generate an iOS/macOS configuration profile (.mobileconfig) that installs
# the stack's private root CA, so Apple devices trust https://JELLYFIN_HOST
# etc. in mesh/LAN mode with no certificate warnings.
#
# Usage:  ./scripts/ios-trust-profile.sh          # writes plextra-trust.mobileconfig
# Send the file to the iPhone/iPad (AirDrop/email), tap it, then:
#   Settings -> Profile Downloaded -> Install
#   Settings -> General -> About -> Certificate Trust Settings -> enable Plextra CA
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/lib.sh
load_env

out="plextra-trust.mobileconfig"

pem=$(docker compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt) \
    || { echo "Could not read the Caddy root CA — is the stack running?" >&2; exit 1; }

# The PEM body is already base64-encoded DER, which is what <data> wants.
b64=$(printf '%s\n' "${pem}" | sed '/-----/d')

uuid() { cat /proc/sys/kernel/random/uuid; }
u1=$(uuid); u2=$(uuid)

cat > "${out}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadCertificateFileName</key><string>plextra-root.crt</string>
      <key>PayloadContent</key>
      <data>
${b64}
      </data>
      <key>PayloadDescription</key><string>Trusts the Plextra server's private certificate authority.</string>
      <key>PayloadDisplayName</key><string>Plextra Root CA</string>
      <key>PayloadIdentifier</key><string>com.plextra.rootca.cert</string>
      <key>PayloadType</key><string>com.apple.security.root</string>
      <key>PayloadUUID</key><string>${u1}</string>
      <key>PayloadVersion</key><integer>1</integer>
    </dict>
  </array>
  <key>PayloadDescription</key><string>Trust profile for your Plextra media server (${JELLYFIN_HOST}).</string>
  <key>PayloadDisplayName</key><string>Plextra Server Trust</string>
  <key>PayloadIdentifier</key><string>com.plextra.rootca</string>
  <key>PayloadRemovalDisallowed</key><false/>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadUUID</key><string>${u2}</string>
  <key>PayloadVersion</key><integer>1</integer>
</dict>
</plist>
EOF

echo "Wrote ${out}"
echo "AirDrop or email it to each Apple device, install it, then enable full"
echo "trust: Settings -> General -> About -> Certificate Trust Settings."
