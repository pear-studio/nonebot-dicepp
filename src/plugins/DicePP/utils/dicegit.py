from typing import Union
from git import InvalidGitRepositoryError, NoSuchPathError, GitCommandError, Repo
from utils.logger import dice_log

IS_NEWEST = False


def is_network_error(err_str: str) -> bool:
    return "errno 10054" in err_str or "Timed out" in err_str or "Could not resolve host" in err_str


class GitRepository:
    """
    git仓库管理
    """

    def __init__(self, local_path, repo_url, update_source):
        try:
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
        self.update_source = update_source
        self.repo = self.get_git_repo()

    def is_dirty_check(self) -> str:
        if self.repo.is_dirty() and self.repo.head.name == "HEAD" and self.repo.remote().name == "origin":
            return "检测到DicePP源码被修改.若需清除该修改,请键入 .update 初始化 以清除其它更改."
        else:
            return ""

    def update(self) -> str:
        marking = self.get_update()
        if marking == "已是最新. ":
            return "已是最新."
        try:
            self.repo.git.fetch(self.repo_url + " " + "refs/heads/master:refs/heads/origin/master")
        except ValueError as e:
            return str(e)
        except GitCommandError as e:
            if e.stderr and is_network_error(e.stderr):
                return "网络连接异常, 请确认没有开启VPN或代理服务, 并再次重试"
            return str(e)
        try:
            self.repo.git.merge("origin/master")
        except GitCommandError as e:
            return str(e)
        return "更新完成,请输入.m reboot重启bot以应用更新."

    def get_update(self) -> str:
        try:
            c = self.repo.git.log("master..origin/master", "-1", "--pretty={hash:%H,%s}")
        except Exception as e:
            return "检查更新失败. 原因: \n" + str(e)
        if c:
            return "检测到更新, 内容如下: \n" + str(c)
        return "已是最新. "

    def refresh(self) -> str:
        try:
            self.repo.git.refresh()
        except Exception as e:
            return "初始化代码失败：" + str(e)
        return "已成功初始化DicePP代码."

    def get_git_repo(self) -> Union[Repo, str]:
        try:
            git_repo = Repo(self.local_path)
        except InvalidGitRepositoryError as e:
            return "git仓库初始化失败, 原因如下: \n" + str(e)
        except NoSuchPathError as e:
            return "git仓库初始化失败, 原因如下: \n" + str(e)
        return git_repo

