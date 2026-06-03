// Reverse-proxy archiviste.nocilia.fr → the Cloud Run gateway.
//
// Cloud Run's frontend routes by Host header and only recognizes the *.run.app
// hostname; a forwarded visitor Host of archiviste.nocilia.fr 404s before
// reaching the gateway. Cloudflare's Origin Rule "Host Header Override" would fix
// this, but it requires a paid plan (Free returns "not entitled"). This Worker
// achieves the same on the Free plan: it rebuilds the request URL with the
// run.app hostname, so the outbound fetch derives Host AND SNI from that URL and
// Cloud Run routes the request to the gateway service. Path, method, headers and
// body pass through unchanged; the response (incl. Set-Cookie) is returned as-is.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    url.protocol = "https:";
    url.hostname = env.ORIGIN_HOST;
    url.port = "";
    return fetch(new Request(url, request));
  },
};
