"use strict";

const http = require("node:http");
const https = require("node:https");

function requestJson(url, options = {}) {
  const {
    method = "GET",
    headers = {},
    body = undefined,
    timeoutMs = 15000,
    tlsRejectUnauthorized = true,
  } = options;

  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const isHttps = target.protocol === "https:";
    const client = isHttps ? https : http;
    const req = client.request(
      target,
      {
        method,
        headers,
        rejectUnauthorized: isHttps ? tlsRejectUnauthorized : undefined,
      },
      (res) => {
        let raw = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          raw += chunk;
        });
        res.on("end", () => {
          const status = res.statusCode || 0;
          const statusText = res.statusMessage || "";
          let json = null;
          if (raw) {
            try {
              json = JSON.parse(raw);
            } catch {
              json = null;
            }
          }
          resolve({
            ok: status >= 200 && status < 300,
            status,
            statusText,
            text: raw,
            json,
          });
        });
      },
    );

    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`request timeout after ${timeoutMs}ms`));
    });
    req.on("error", reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

module.exports = {
  requestJson,
};
