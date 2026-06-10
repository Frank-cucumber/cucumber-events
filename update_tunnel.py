"""
Starts the localhost.run tunnel, captures the URL,
updates the GitHub Pages redirect, then keeps the tunnel alive.
"""
import subprocess, re, requests, base64, sys, os

GH_TOKEN = os.environ.get("GH_TOKEN", "")
SSH_KEY  = os.path.expanduser("~/.ssh/id_rsa")
REPO     = "Frank-cucumber/cucumber-events-link"

def get_gh_token():
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        return r.stdout.strip()
    except Exception:
        return GH_TOKEN

def update_github_redirect(tunnel_url, token):
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="0;url={tunnel_url}">
<title>Cucumber Events Studio</title>
</head>
<body><p>Opening Cucumber Events Studio... <a href="{tunnel_url}">Click here if not redirected.</a></p></body>
</html>"""
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(f"https://api.github.com/repos/{REPO}/contents/index.html", headers=headers)
    sha = r.json().get("sha", "")
    payload = {"message": "Update tunnel URL", "content": base64.b64encode(html.encode()).decode()}
    if sha:
        payload["sha"] = sha
    r2 = requests.put(f"https://api.github.com/repos/{REPO}/contents/index.html",
                      headers=headers, json=payload)
    if r2.status_code in (200, 201):
        print(f"GitHub Pages updated -> https://frank-cucumber.github.io/cucumber-events-link/")
    else:
        print(f"GitHub update failed: {r2.status_code}")

def main():
    token = get_gh_token()
    print("Starting tunnel...")
    proc = subprocess.Popen(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "ServerAliveInterval=30", "-R", "80:localhost:5000", "ssh.localhost.run"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for line in proc.stdout:
        line = line.strip()
        if line:
            print(line)
        m = re.search(r"https://[a-f0-9]+\.lhr\.life", line)
        if m:
            tunnel_url = m.group(0)
            print(f"\nTunnel URL: {tunnel_url}")
            if token:
                update_github_redirect(tunnel_url, token)
            break
    proc.wait()

if __name__ == "__main__":
    main()
