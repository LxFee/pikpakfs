import asyncio
import threading
import nest_asyncio



async def aoutput(output):
    print(output)

# 定义一个函数来启动事件循环
def start_event_loop(mainloop, newloop):
    asyncio.set_event_loop(newloop)
    newloop.run_forever()

async def myinput(newloop):
    future = asyncio.run_coroutine_threadsafe(asyncio.con(input("input")), newloop)
    return await asyncio.wrap_future(future)

async def myoutput(newloop, output):
    future = asyncio.run_coroutine_threadsafe(aoutput(output), newloop)
    await asyncio.wrap_future(future)

async def main(mainloop, newloop):
    while True:
        res = await myinput(newloop)
        await myoutput(newloop, res)

if __name__ == "__main__":
    nest_asyncio.apply()

    # 创建一个新的事件循环
    main_loop = asyncio.get_event_loop()
    new_loop = asyncio.new_event_loop()

    # 使用 threading 模块创建一个新线程，并在该线程中运行事件循环
    thread = threading.Thread(target=start_event_loop, args=(main_loop, new_loop,))
    thread.start()

    asyncio.run(main(main_loop, new_loop))

    # 等待线程完成
    thread.join()
