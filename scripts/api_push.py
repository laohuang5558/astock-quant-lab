"""通过 GitHub Git Data API 推送本地 HEAD 的变更（github.com 不可达时的通道）。

前提：本地 HEAD 的 parent == 远端 master 当前 commit（快进场景）。
只推送本地相对父提交的差异文件，其余树节点沿用远端。
"""
import base64
import json
import os
import subprocess
import urllib.request
import urllib.error

TOKEN = open(os.environ["LOCALAPPDATA"] + "/Temp/gh_token.tmp", encoding="utf-8").read().strip()
HEADERS = {
    "User-Agent": "quantlab-api-push",
    "Authorization": "Bearer " + TOKEN,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
REPO = "/repos/laohuang5558/astock-quant-lab"
API = "https://api.github.com"


def api(path, method="GET", payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=body, headers=HEADERS, method=method)
    return json.load(urllib.request.urlopen(req, timeout=60))


def git(*args):
    return subprocess.check_output(["git", *args], cwd=os.path.dirname(os.path.abspath(__file__)) + "/..").decode("utf-8").strip()


# 本地 HEAD 信息
head_sha = git("rev-parse", "HEAD")
parent_sha = git("rev-parse", "HEAD^")
commit_msg = git("log", "-1", "--pretty=%B")

# 远端 master
ref = api(REPO + "/git/ref/heads/master")
remote_sha = ref["object"]["sha"]
remote_commit = api(REPO + "/git/commits/" + remote_sha)
remote_tree_sha = remote_commit["tree"]["sha"]

print("local HEAD   =", head_sha)
print("local parent =", parent_sha)
print("remote master=", remote_sha)
assert parent_sha == remote_sha, "本地 HEAD 的父提交不是远端 master，需先同步，拒绝覆盖式推送"

# 本地相对父提交的变更文件（A/M/D）
diff = git("diff", "--name-status", parent_sha, head_sha).splitlines()
print("changed files=", len(diff))

# 获取远端完整树（递归），以此为基础修改
remote_tree = api(REPO + "/git/trees/" + remote_tree_sha + "?recursive=1")
tree_map = {e["path"]: e for e in remote_tree["tree"]}

changes = []
for line in diff:
    parts = line.split("\t")
    status = parts[0][0]
    path = parts[1]
    if status == "D":
        changes.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        continue
    # 读取本地文件的 git blob（保留二进制一致性）
    blob_sha = git("rev-parse", head_sha + ":" + path)
    mode = "100755" if git("ls-tree", head_sha, "--", path).startswith("100755") else "100644"
    if path in tree_map and tree_map[path].get("mode") == "100755":
        mode = "100755"
    changes.append({"path": path, "mode": mode, "type": "blob", "sha": blob_sha, "_local": True})

# 本地 blob SHA 与远端对象库一致（同为 git Content-Addressable），无需重新上传内容；
# 但 GitHub Git Data API 要求 blob 对象存在于其对象库。先检查哪些 SHA 缺失。
need_upload = [c for c in changes if c.get("_local")]
missing = []
for c in need_upload:
    try:
        api(REPO + "/git/blobs/" + c["sha"])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            missing.append(c)
        else:
            raise

for c in missing:
    # 从 git 对象库直接读取 commit 中的 blob 字节（二进制安全，不依赖工作区当前状态）
    data = subprocess.check_output(["git", "cat-file", "blob", c["sha"]],
                                   cwd=git("rev-parse", "--show-toplevel"))
    b = api(REPO + "/git/blobs", "POST", {
        "content": base64.b64encode(data).decode("ascii"),
        "encoding": "base64",
    })
    assert b["sha"] == c["sha"], f"blob sha 不一致 {c['path']}: {b['sha']} vs {c['sha']}"
    print("  uploaded blob", c["path"][:50], c["sha"][:10])

# 构造新 tree
tree_entries = []
for c in changes:
    e = {"path": c["path"], "mode": c["mode"], "type": "blob"}
    if c["sha"] is None:
        continue  # 删除：不放入新 tree
    e["sha"] = c["sha"]
    tree_entries.append(e)

new_tree = api(REPO + "/git/trees", "POST", {
    "base_tree": remote_tree_sha,
    "tree": tree_entries,
})
print("new tree =", new_tree["sha"][:12])

# 创建 commit（复用本地 commit message 与 author/committer 及原始时间，保证 sha 一致）
author_raw = git("log", "-1", "--pretty=%an|%ae|%aI")
an, ae, adate = author_raw.split("|")
committer_raw = git("log", "-1", "--pretty=%cn|%ce|%cI")
cn, ce, cdate = committer_raw.split("|")
new_commit = api(REPO + "/git/commits", "POST", {
    "message": commit_msg,
    "tree": new_tree["sha"],
    "parents": [remote_sha],
    "author": {"name": an, "email": ae, "date": adate},
    "committer": {"name": cn, "email": ce, "date": cdate},
})
print("new commit =", new_commit["sha"][:12])

# 更新 ref
updated = api(REPO + "/git/refs/heads/master", "PATCH", {"sha": new_commit["sha"], "force": False})
print("ref updated ->", updated["object"]["sha"][:12])
print("DONE local_head=", head_sha[:12], "remote=", new_commit["sha"][:12])
assert new_commit["tree"]["sha"] == git("rev-parse", "HEAD^{tree}"), "远端树与本地 HEAD 树内容不一致"
