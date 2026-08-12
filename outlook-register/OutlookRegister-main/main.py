import os
import sys
import time
import json
from get_token import get_access_token
from concurrent.futures import ThreadPoolExecutor
from utils import random_email, generate_strong_password
from controllers.patchright_controller import PatchrightController
from controllers.playwright_controller import PlaywrightController


class _Tee:
    """print 同时输出到终端和 Results/register_run.log（供可视化面板读取）"""

    def __init__(self, path):
        self._path = path
        self._term = sys.stdout

    def write(self, msg):
        # 终端编码（如 GBK）不兼容时降级为替换字符，绝不让日志写入崩溃整个流程
        try:
            self._term.write(msg)
        except Exception:
            try:
                enc = getattr(self._term, "encoding", None) or "utf-8"
                safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
                self._term.write(safe)
            except Exception:
                pass
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass

    def flush(self):
        try:
            self._term.flush()
        except Exception:
            pass



# --- 不确定有无帮助 ---
# 0. 视窗大小
# 1. CDP 检测：wait_for_timeout --> time.sleep()
# 2. 使用 launch_persistent_context 
# 3. 避免短时间访问
# 4. 模拟真人轨迹
# 时区

def process_single_flow(controller):
    page = None

    try:
        page = controller.get_thread_page()

        email = random_email()
        password = generate_strong_password()

        # 调用 controller 特定的注册方法 
        result = controller.outlook_register(page, email, password)

        if result and not controller.enable_oauth2:
            return True
        elif not result:
            return False

        token_result = get_access_token(page, email)
        if token_result[0]:
            refresh_token, access_token, expire_at =  token_result
            results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Results')

            # 统一格式（accounts.txt）：email----password----client_id----refresh_token
            # 辅助邮箱验证结束后按此格式落盘，一个账号一行，方便整理
            try:
                with open('config.json', 'r', encoding='utf-8') as f3:
                    _cfg = json.load(f3)
                client_id = _cfg.get('oauth2', {}).get('client_id', '')
            except Exception:
                client_id = ''
            with open(os.path.join(results_dir, 'accounts.txt'), 'a', encoding='utf-8') as f4:
                f4.write(f"{email}{controller.email_suffix}----{password}----{client_id}----{refresh_token}\n")

            # 旧格式（outlook_token.txt）：email---password---refresh_token---access_token---expire_at
            with open(os.path.join(results_dir, 'outlook_token.txt'), 'a', encoding='utf-8') as f2:
                f2.write(f"{email}{controller.email_suffix}---{password}---{refresh_token}---{access_token}---{expire_at}\n")

            print(f'[Success: TokenAuth] - {email}{controller.email_suffix}')
            return True
        else:
            return False

    except Exception as e:
        print(e)
        return False
    
    finally:

        controller.clean_up(page, "done_browser")

def run_concurrent_flows(controller, concurrent_flows=10, max_tasks=100):
    task_counter = 0
    succeeded_tasks = 0
    failed_tasks = 0

    with ThreadPoolExecutor(max_workers=concurrent_flows) as executor:
        running_futures = set()

        while task_counter < max_tasks or len(running_futures) > 0:
            done_futures = {f for f in running_futures if f.done()}
            for future in done_futures:
                try:
                    if future.result():
                        succeeded_tasks += 1
                    else:
                        failed_tasks += 1
                except Exception as e:
                    failed_tasks += 1
                    print(e)
                running_futures.remove(future)

            while len(running_futures) < concurrent_flows and task_counter < max_tasks:
                new_future = executor.submit(process_single_flow, controller)
                running_futures.add(new_future)
                task_counter += 1
                if max_tasks > 1 and task_counter % (max_tasks // 2) == 0:
                    print(f"已提交 {task_counter}/{max_tasks} 任务.")
                elif max_tasks == 1:
                    print(f"已提交 {task_counter}/{max_tasks} 任务.")

            time.sleep(0.5)

    print(f"\n[Result] - 共: {max_tasks}, 成功 {succeeded_tasks}, 失败 {failed_tasks}")


if __name__ == "__main__":

    with open('config.json', 'r', encoding='utf-8') as f:
        data = json.load(f) 
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Results')
    os.makedirs(results_dir, exist_ok=True)
    # 运行日志：终端 + Results/register_run.log 双写
    sys.stdout = _Tee(os.path.join(results_dir, 'register_run.log'))

    max_tasks = data["max_tasks"]
    concurrent_flows = data["concurrent_flows"]

    if data["choose_browser"] =="patchright":
        selected_controller = PatchrightController()
    elif data["choose_browser"] =="playwright":
        selected_controller = PlaywrightController()
    else:
        print("不支持的浏览器类型，填写patchright或者playwright")
  

    try:
        run_concurrent_flows(selected_controller, concurrent_flows, max_tasks)
    finally:
        selected_controller.clean_up(type="all_browser")