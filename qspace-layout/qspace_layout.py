#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QSpace 桌面布局管理器 v1
管理 QSpace Pro 内置桌面的图标布局快照（多套方案保存/恢复）

用法:
  python3 qspace_layout.py save "外接屏"     # 把当前桌面布局保存为"外接屏"
  python3 qspace_layout.py restore "外接屏"  # 恢复"外接屏"布局（会重启 QSpace Pro，约 8 秒）
  python3 qspace_layout.py list              # 列出所有已保存的布局
  python3 qspace_layout.py delete "外接屏"   # 删除指定布局
  python3 qspace_layout.py show "外接屏"     # 查看布局详情（保存时间/屏幕/图标数）
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

CONF_PATH = os.path.expanduser(
    '~/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf')
LAYOUT_DIR = os.path.expanduser('~/qspace-layouts/layouts')
QSPACE_APP = '/Applications/QSpace Pro.app'


def load_conf():
    with open(CONF_PATH) as f:
        return json.load(f)


def write_conf(conf):
    with open(CONF_PATH, 'w') as f:
        json.dump(conf, f, ensure_ascii=False)


def layout_path(name):
    return os.path.join(LAYOUT_DIR, f'{name}.json')


def qspace_running():
    return subprocess.run(['pgrep', '-x', 'QSpace Pro'],
                          capture_output=True).returncode == 0


def restart_qspace():
    """重启 QSpace Pro 使 conf 生效（QSpace 启动时读一次，运行中不重读）"""
    subprocess.run(['killall', 'QSpace Pro'], capture_output=True)
    time.sleep(3)
    subprocess.run(['open', QSPACE_APP], capture_output=True)
    for _ in range(20):
        time.sleep(1)
        if subprocess.run(['pgrep', '-x', 'QSpace Pro'],
                          capture_output=True).returncode == 0:
            time.sleep(3)  # 等桌面渲染稳定
            return True
    return False


def current_screens(conf):
    """从 conf 推断当前活跃的屏幕集合"""
    return sorted({str(i.get('screen', '?'))
                   for i in conf.get('itemAttrs', [])})


def cmd_save(name):
    if not qspace_running():
        print('❌ QSpace Pro 没在运行，无法保存当前布局')
        sys.exit(1)
    conf = load_conf()
    snapshot = {
        'name': name,
        'savedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'screens': current_screens(conf),
        'iconCount': len(conf.get('itemAttrs', [])),
        'itemAttrs': conf.get('itemAttrs', []),
        'itemVSpacing': conf.get('itemVSpacing', 0),
        'showItemBriefInfo': conf.get('showItemBriefInfo', True),
    }
    os.makedirs(LAYOUT_DIR, exist_ok=True)
    with open(layout_path(name), 'w') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    print(f'✅ 已保存布局「{name}」: {snapshot["iconCount"]} 个图标, '
          f'屏幕 {snapshot["screens"]}')


def cmd_restore(name):
    lp = layout_path(name)
    if not os.path.exists(lp):
        print(f'❌ 布局「{name}」不存在。用 list 查看已有布局')
        sys.exit(1)
    with open(lp) as f:
        snap = json.load(f)

    snap_index = {item['name']: item for item in snap['itemAttrs']}
    conf = load_conf()

    matched, kept = 0, 0
    new_attrs = []
    for item in conf.get('itemAttrs', []):
        if item['name'] in snap_index:
            s = snap_index[item['name']]
            item['x'] = s['x']
            item['y'] = s['y']
            item['screen'] = s['screen']
            new_attrs.append(item)
            matched += 1
        else:
            new_attrs.append(item)  # 快照之后新增的文件，保留现位置
            kept += 1

    conf['itemAttrs'] = new_attrs
    conf['itemVSpacing'] = snap.get('itemVSpacing', conf.get('itemVSpacing', 0))
    conf['showItemBriefInfo'] = snap.get('showItemBriefInfo',
                                         conf.get('showItemBriefInfo', True))
    write_conf(conf)
    print(f'📦 布局「{name}」已写入: 匹配恢复 {matched} 个, '
          f'保留新增 {kept} 个（快照时间 {snap["savedAt"]}）')

    print('🔄 重启 QSpace Pro 应用新布局（约 8 秒）...')
    if restart_qspace():
        print('✅ 完成！')
    else:
        print('⚠️ QSpace Pro 未能确认启动，请手动打开')


def cmd_list():
    if not os.path.isdir(LAYOUT_DIR):
        print('（还没有任何保存的布局）')
        return
    files = sorted(f for f in os.listdir(LAYOUT_DIR) if f.endswith('.json'))
    if not files:
        print('（还没有任何保存的布局）')
        return
    print('已保存的布局:')
    for fn in files:
        try:
            with open(os.path.join(LAYOUT_DIR, fn)) as f:
                s = json.load(f)
            print(f'  「{s["name"]}」 {s["savedAt"]}  '
                  f'{s["iconCount"]} 图标, 屏幕 {s["screens"]}')
        except Exception as e:
            print(f'  {fn}: 读取失败 ({e})')


def cmd_delete(name):
    lp = layout_path(name)
    if os.path.exists(lp):
        os.remove(lp)
        print(f'🗑️ 已删除布局「{name}」')
    else:
        print(f'❌ 布局「{name}」不存在')


def cmd_show(name):
    lp = layout_path(name)
    if not os.path.exists(lp):
        print(f'❌ 布局「{name}」不存在')
        sys.exit(1)
    with open(lp) as f:
        s = json.load(f)
    print(f'布局「{s["name"]}」')
    print(f'  保存时间: {s["savedAt"]}')
    print(f'  图标数量: {s["iconCount"]}')
    print(f'  屏幕编号: {s["screens"]}')
    print(f'  前 5 项:')
    for item in s['itemAttrs'][:5]:
        nm = os.path.basename(item['name'].rstrip('/'))
        print(f'    {nm}: ({item["x"]}, {item["y"]}) @ 屏{item["screen"]}')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    actions = {
        'save': lambda: cmd_save(arg) if arg else print('用法: save <名称>'),
        'restore': lambda: cmd_restore(arg) if arg else print('用法: restore <名称>'),
        'list': cmd_list,
        'delete': lambda: cmd_delete(arg) if arg else print('用法: delete <名称>'),
        'show': lambda: cmd_show(arg) if arg else print('用法: show <名称>'),
    }
    if cmd in actions:
        actions[cmd]()
    else:
        print(f'未知命令: {cmd}')
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
