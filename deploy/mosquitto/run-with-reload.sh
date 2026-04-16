#!/bin/sh
set -eu

CONFIG_DIR="/mosquitto/config-live"
PASSWORD_FILE="$CONFIG_DIR/passwordfile"
ACL_FILE="$CONFIG_DIR/aclfile"
STAMP_FILE="$CONFIG_DIR/reload.stamp"
CONFIG_FILE="$CONFIG_DIR/mosquitto.conf"

mkdir -p "$CONFIG_DIR"
touch "$PASSWORD_FILE" "$ACL_FILE" "$STAMP_FILE"

mtime() {
  stat -c %Y "$1" 2>/dev/null || echo 0
}

last_password="$(mtime "$PASSWORD_FILE")"
last_acl="$(mtime "$ACL_FILE")"
last_stamp="$(mtime "$STAMP_FILE")"

mosquitto -c "$CONFIG_FILE" &
MQTT_PID="$!"

shutdown() {
  kill -TERM "$MQTT_PID" 2>/dev/null || true
  wait "$MQTT_PID" 2>/dev/null || true
}

trap shutdown INT TERM

while kill -0 "$MQTT_PID" 2>/dev/null; do
  sleep 2
  next_password="$(mtime "$PASSWORD_FILE")"
  next_acl="$(mtime "$ACL_FILE")"
  next_stamp="$(mtime "$STAMP_FILE")"
  if [ "$next_password" != "$last_password" ] || [ "$next_acl" != "$last_acl" ] || [ "$next_stamp" != "$last_stamp" ]; then
    kill -HUP "$MQTT_PID"
    last_password="$next_password"
    last_acl="$next_acl"
    last_stamp="$next_stamp"
  fi
done

wait "$MQTT_PID"
