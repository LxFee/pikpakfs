import cmd2
import sys
from pikpakFs import PKVirtFs, VirtFsNode, DirNode, FileNode


class PKApp(cmd2.Cmd):
    def __init__(self):
        super().__init__()
        self.fs = PKVirtFs(loginCachePath = "token.json", proxy = "http://127.0.0.1:10808")

    async def do_login(self, args):
        if len(args) < 2:
            await self.fs.Login()
        else:
            await self.fs.Login(args[0], args[1])
        


if __name__ == '__main__':
    app = PKApp()
    sys.exit(app.cmdloop())