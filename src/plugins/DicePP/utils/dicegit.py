from utils.logger import dice_log


def is_network_error(err_str: str) -> bool:
    # if "Could not resolve host" in err_str:
    #     print(self.TextDict["ResolveHostException"])
    return "errno 10054" in err_str or "Timed out" in err_str or "Could not resolve host" in err_str


class GitRepository(object):
    """
    git仓库管理
    """

    def __init__(self, local_path, repo_url, update_source):
        try:
            import nonebot
            import openpyxl
            import rsa
            import yaml
            import os
            import git
        except ImportError as e:
            raise AssertionError(f"当前Python环境缺少必要库\n{e}")

        try:
            assert git.GIT_OK
        except AssertionError:
            git_path = os.path.abspath("../PortableGit/bin/git.exe")
            assert os.path.exists(git_path)
            dice_log(f"找不到系统已安装的Git工具, 使用PortableGit: {git_path}")
            git.refresh(git_path)
        assert git.GIT_OK, "Git不可用..."
        self.local_path = local_path
        self.repo_url = repo_url
        self.TextDict = {"CheckDone": "检测到新版本,更新内容如下：",
                         "ManualFetchDone": "已为您下载最新版本更新.",
                         "NetworkException": "网络连接异常，请确认没有开启VPN或代理服务，并再次重试。",
                         "SourceCodeChanged": "检测到DicePP源码被修改。若需恢复，请键入.update resource-code",
                         "ResolveHostException": "解析IP地址失败，请尝试修改host文件。",
                         "UpdateDone": "更新完成!",
                         "IsNewest": "已是最新",
                         "SourceCodeRefresh": "所有代码更新已清除。", }
        self.repo = git.Repo(self.local_path)
        self.update_source = "gitee"

    def is_dirty_check(self) -> str:
        if self.repo.is_dirty() and self.repo.head.name == "HEAD" and self.repo.remote().name == "origin":
            return self.TextDict["SourceCodeChanged"]

    def update(self):
        master = self.repo.heads.master
        other = self.repo.create_head("other", "head")
        other.checkout()
        self.repo.remote().fetch()
        master.checkout()
        self.repo.index.merge_tree(other)
        self.repo.merge_base()
        other.delete(self.repo, other)
        return self.TextDict["UpdateDone"]

    def get_update(self):
        c = self.repo.git.log("master..origin/master", "-1", "--pretty={format:%H,%s}")
        if c:
            return self.TextDict["CheckDone"], c
        return self.TextDict["IsNewest"]

    def refresh(self):
        self.repo.git.refresh()
        return self.TextDict["SourceCodeRefresh"]


if __name__ == "__main__":
    import os
    import git

    test = GitRepository(r"D:/tmp/dicepp", "https://gitee.com/pear_studio/nonebot-dicepp.git", "gitee")
    test.get_update()
    test.update()
