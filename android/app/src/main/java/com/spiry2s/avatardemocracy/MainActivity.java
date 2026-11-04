package com.spiry2s.avatardemocracy;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * Two modes:
 *
 * STANDALONE (default) — the bundled frontend is served from APK assets on a
 * private https origin. Injected scripts (assets/app/*.js) reimplement the
 * backend in JavaScript: state in localStorage, Claude called directly with
 * the citizen's own API key. No PC needed.
 *
 * REMOTE — enter a server URL (e.g. http://192.168.1.50:8000) and the app is a
 * thin client for the Python backend, exactly as before.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "avatar_democracy";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String KEY_MODE = "mode"; // "standalone" | "remote"
    private static final String DEFAULT_SERVER_URL = "http://10.0.2.2:8000";

    /** Private origin for APK-asset pages. Never resolves on the network. */
    private static final String ASSET_HOST = "appassets.androidplatform.net";
    private static final String ASSET_ORIGIN = "https://" + ASSET_HOST;

    /** Injected right after <head> so the shim wraps fetch before page scripts run. */
    private static final String INJECT_TAGS =
            "<script src=\"/app/store.js\"></script>"
            + "<script src=\"/app/prompts.js\"></script>"
            + "<script src=\"/app/llm.js\"></script>"
            + "<script src=\"/app/localapi.js\"></script>"
            + "<script src=\"/app/shim.js\"></script>";

    private WebView webView;
    private LinearLayout setupView;
    private EditText serverInput;
    private ProgressBar progress;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        buildUi();
        if ("remote".equals(prefs.getString(KEY_MODE, "standalone"))) {
            loadRemote();
        } else {
            loadStandalone();
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(15, 17, 23));

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(12), dp(10), dp(12), dp(10));
        bar.setBackgroundColor(Color.rgb(26, 29, 39));

        serverInput = new EditText(this);
        serverInput.setSingleLine(true);
        serverInput.setTextColor(Color.rgb(232, 234, 240));
        serverInput.setHintTextColor(Color.rgb(124, 130, 160));
        serverInput.setHint("Server URL (optional)");
        serverInput.setText(getServerUrl());
        serverInput.setImeOptions(EditorInfo.IME_ACTION_GO);
        serverInput.setTextSize(13);

        Button go = new Button(this);
        go.setText("Go");
        go.setOnClickListener(v -> saveAndLoadRemote());

        Button local = new Button(this);
        local.setText("Local");
        local.setOnClickListener(v -> {
            prefs.edit().putString(KEY_MODE, "standalone").apply();
            loadStandalone();
        });

        bar.addView(serverInput, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        bar.addView(go);
        bar.addView(local);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setIndeterminate(true);
        progress.setVisibility(View.GONE);

        setupView = buildSetupView();

        webView = new WebView(this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true); // localStorage holds all standalone state
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMediaPlaybackRequiresUserGesture(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (ASSET_HOST.equals(uri.getHost())) {
                    return serveAsset(uri.getPath());
                }
                return null; // network (remote mode pages, api.anthropic.com)
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                progress.setVisibility(View.VISIBLE);
                setupView.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, android.webkit.WebResourceError error) {
                // Only a remote server can be unreachable; assets always load.
                if (request.isForMainFrame() && !ASSET_HOST.equals(request.getUrl().getHost())) {
                    showSetup();
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme();
                if (!"http".equals(scheme) && !"https".equals(scheme)) return true; // drop exotic schemes
                boolean standalone = ASSET_HOST.equals(Uri.parse(view.getUrl() == null ? "" : view.getUrl()).getHost());
                if (standalone && !ASSET_HOST.equals(uri.getHost())) {
                    // External links (e.g. GitHub) open in the system browser.
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    } catch (Exception ignored) { }
                    return true;
                }
                return false;
            }
        });

        root.addView(bar);
        root.addView(progress, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(3)));
        root.addView(setupView, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        root.addView(webView, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
    }

    // ── standalone asset server ──────────────────────────────────────────────

    /** Map a clean URL path to a bundled asset and serve it, injecting the
     *  standalone scripts into HTML documents. */
    private WebResourceResponse serveAsset(String path) {
        if (path == null || path.isEmpty() || "/".equals(path)) path = "/index.html";

        String assetName;
        if (path.startsWith("/static/")) {
            assetName = path.substring("/static/".length());
        } else if (path.startsWith("/app/")) {
            assetName = path.substring(1); // assets/app/*.js
        } else {
            assetName = path.substring(1);
            if (!assetName.contains(".")) assetName += ".html"; // "/analyze" -> analyze.html
        }

        try {
            InputStream in = getAssets().open(assetName);
            if (assetName.endsWith(".html")) {
                String html = readAll(in);
                html = html.replaceFirst("(?i)<head>", "<head>" + INJECT_TAGS);
                byte[] bytes = html.getBytes(StandardCharsets.UTF_8);
                return new WebResourceResponse("text/html", "utf-8", new ByteArrayInputStream(bytes));
            }
            return new WebResourceResponse(mimeFor(assetName), "utf-8", in);
        } catch (IOException e) {
            byte[] body = ("Not found: " + assetName).getBytes(StandardCharsets.UTF_8);
            WebResourceResponse resp = new WebResourceResponse("text/plain", "utf-8", new ByteArrayInputStream(body));
            resp.setStatusCodeAndReasonPhrase(404, "Not Found");
            return resp;
        }
    }

    private static String readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
        in.close();
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }

    private static String mimeFor(String name) {
        if (name.endsWith(".js")) return "text/javascript";
        if (name.endsWith(".css")) return "text/css";
        if (name.endsWith(".json")) return "application/json";
        if (name.endsWith(".svg")) return "image/svg+xml";
        if (name.endsWith(".png")) return "image/png";
        if (name.endsWith(".jpg") || name.endsWith(".jpeg")) return "image/jpeg";
        if (name.endsWith(".ico")) return "image/x-icon";
        if (name.endsWith(".woff") || name.endsWith(".woff2")) return "font/woff2";
        return "application/octet-stream";
    }

    // ── modes ────────────────────────────────────────────────────────────────

    private void loadStandalone() {
        setupView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(ASSET_ORIGIN + "/");
    }

    private void saveAndLoadRemote() {
        String value = normalizeUrl(serverInput.getText().toString());
        prefs.edit().putString(KEY_SERVER_URL, value).putString(KEY_MODE, "remote").apply();
        serverInput.setText(value);
        loadRemote();
    }

    private void loadRemote() {
        setupView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(getServerUrl());
    }

    private LinearLayout buildSetupView() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(dp(24), dp(24), dp(24), dp(24));
        box.setBackgroundColor(Color.rgb(15, 17, 23));

        TextView title = new TextView(this);
        title.setText("Server unreachable");
        title.setTextColor(Color.rgb(232, 234, 240));
        title.setTextSize(22);
        title.setGravity(Gravity.CENTER);

        TextView body = new TextView(this);
        body.setText("Could not reach that server URL.\n\nCheck the address and Wi-Fi, or tap Local to use the standalone app on this phone (no PC needed).");
        body.setTextColor(Color.rgb(124, 130, 160));
        body.setTextSize(14);
        body.setGravity(Gravity.CENTER);
        body.setPadding(0, dp(14), 0, dp(18));

        Button retry = new Button(this);
        retry.setText("Retry server");
        retry.setOnClickListener(v -> saveAndLoadRemote());

        Button local = new Button(this);
        local.setText("Use standalone");
        local.setOnClickListener(v -> {
            prefs.edit().putString(KEY_MODE, "standalone").apply();
            loadStandalone();
        });

        box.addView(title);
        box.addView(body);
        box.addView(retry);
        box.addView(local);
        return box;
    }

    private void showSetup() {
        progress.setVisibility(View.GONE);
        webView.setVisibility(View.GONE);
        setupView.setVisibility(View.VISIBLE);
    }

    private String getServerUrl() {
        return prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);
    }

    private String normalizeUrl(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) value = DEFAULT_SERVER_URL;
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://" + value;
        }
        return value;
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
