# -*- coding: utf-8 -*-
"""
串口协议编解码 —— PC-C 上位机 <-> STM32 风机控制器（USART2）

帧格式（ASCII，\\r\\n 结尾，字段以 , 分隔，无校验）：

  旧帧（保持兼容）:
    MCU -> PC 遥测 :  $WIND,<cycle>,<wind_speed_mps>,<wind_available_kw>,<wind_operating_limit_kw>,<wind_target_kw>,<wind_actual_kw>,<wind_running>,<pitch_target_deg>,<control_mode>,<link_status>
    PC  -> MCU 参数:  $PARAM,<cut_in_speed_mps>,<rated_speed_mps>,<cut_out_speed_mps>,<wind_rated_power_kw>,<pitch_feather_deg>,<c_control_s>,<c_timeout_s>,<control_mode>
    PC  -> MCU 命令:  $CMD,START|STOP|RESET|AUTO|MANUAL
    PC  -> MCU WiFi:  $WIFI,<ip>,<port>   /  $WIFI?
    MCU -> PC  应答:  $ACK,<PARAM|CMD|WIFI>,<0|1>   /  $WIFIGET,<ip>,<port>

  新帧（本整改新增，用于参数回读证明与动作追踪）:
    MCU -> PC 遥测 :  $WIND2,<同 $WIND 10 字段>,<last_wind_action_seq>   (-1 表示尚无被 A accepted 的 C 动作)
    PC  -> MCU 参数:  $PARAM2,<request_id>,<8 字段同 $PARAM>
    PC  -> MCU 查询:  $PARAMGET?,<request_id>
    MCU -> PC 读回 :  $PARAMGET,<request_id>,<parameter_revision>,<8 字段>
    MCU -> PC 同步 :  $SYNC,<a_sync_status>,<a_sync_seq>,<a_sync_reason>

字段顺序以 config.PARAM_FIELDS / config.WIND_FIELDS / config.WIND2_FIELDS 为准。
"""

from config import PARAM_FIELDS, WIND_FIELDS, WIND2_FIELDS


def _fmt(v):
    """把数值格式化为字符串：int 转 int，float 保留 2 位。"""
    if isinstance(v, int):
        return str(v)
    return f"{float(v):.2f}"


def build_param_frame(params):
    """根据参数字典生成旧 $PARAM 帧（bytes，兼容保留）。"""
    parts = [_fmt(params[k]) for k in PARAM_FIELDS]
    return ("$PARAM," + ",".join(parts) + "\r\n").encode("ascii")


def build_param2_frame(request_id, params):
    """生成 $PARAM2,<request_id>,<8 字段> 帧（bytes），供原子应用 + 回读验证。"""
    parts = [str(int(request_id))] + [_fmt(params[k]) for k in PARAM_FIELDS]
    return ("$PARAM2," + ",".join(parts) + "\r\n").encode("ascii")


def build_cmd_frame(command):
    """根据命令名（START/STOP/RESET/AUTO/MANUAL）生成 $CMD 帧（bytes）。"""
    return f"$CMD,{command}\r\n".encode("ascii")


def build_wifi_frame(ip, port):
    """生成 $WIFI,<ip>,<port> 设置帧（bytes），让 MCU 更新 A 地址/端口并重连。"""
    return f"$WIFI,{ip},{int(port)}\r\n".encode("ascii")


def build_wifi_query_frame():
    """生成 $WIFI? 查询帧（bytes），查询 MCU 当前 A 地址/端口。"""
    return b"$WIFI?\r\n"


def build_param_query_frame():
    """生成旧 $PARAM? 查询帧（bytes，兼容保留）。"""
    return b"$PARAM?\r\n"


def build_paramget_query_frame(request_id):
    """生成 $PARAMGET?,<request_id> 查询帧（bytes），读回 MCU 当前有效参数。"""
    return f"$PARAMGET?,{int(request_id)}\r\n".encode("ascii")


def _parse_wind_fields(fields, has_seq):
    """解析 $WIND / $WIND2 的数值字段，返回 dict 或 None（字段数不符/非法数字时返回 None）。"""
    names = WIND2_FIELDS if has_seq else WIND_FIELDS
    if len(fields) != len(names):
        return None
    data = {}
    for name, raw in zip(names, fields):
        try:
            if name in ("cycle", "control_mode", "link_status"):
                data[name] = int(float(raw))
            elif name == "wind_running":
                data[name] = bool(int(float(raw)))
            elif name == "last_wind_action_seq":
                seq = int(float(raw))
                data[name] = None if seq < 0 else seq
            else:
                data[name] = float(raw)
        except ValueError:
            return None
    # 旧 $WIND 无动作序号字段 → 统一补 None，便于上层与 $WIND2 一致处理
    if not has_seq:
        data["last_wind_action_seq"] = None
    return data


def parse_frame(line):
    """解析一帧，返回 (类型, 内容) 或 None。

    返回:
      ("wind", dict)     遥测帧（$WIND 或 $WIND2），键含 WIND_FIELDS + last_wind_action_seq
      ("paramget", dict) $PARAMGET 读回帧（含 request_id / parameter_revision / 8 参数）
      ("sync", dict)     $SYNC 同步状态帧（a_sync_status / a_sync_seq / a_sync_reason）
      ("ack",  (type, ok))
      ("cmd", raw)
      ("param", raw)
      ("wifiget", (ip, port))
      ("other", raw)
      None               无法识别
    """
    if not isinstance(line, str):
        try:
            line = line.decode("ascii", errors="ignore")
        except Exception:
            return None
    line = line.strip()
    if not line.startswith("$"):
        return None

    body = line[1:]
    kind, sep, rest = body.partition(",")
    if not sep:
        return None

    if kind == "WIND":
        data = _parse_wind_fields(rest.split(","), has_seq=False)
        return ("wind", data) if data is not None else None

    if kind == "WIND2":
        data = _parse_wind_fields(rest.split(","), has_seq=True)
        return ("wind", data) if data is not None else None

    if kind == "ACK":
        fields = rest.split(",")
        if len(fields) >= 2:
            try:
                return ("ack", (fields[0], int(fields[1])))
            except ValueError:
                return ("ack", (fields[0], 0))
        return None

    if kind == "CMD":
        return ("cmd", rest)
    if kind == "PARAM":
        return ("param", rest)

    if kind == "WIFIGET":
        fields = rest.split(",")
        if len(fields) >= 2:
            return ("wifiget", (fields[0], fields[1]))
        return None

    if kind == "PARAMGET":
        fields = rest.split(",")
        # 新格式：request_id + parameter_revision + 8 参数 = 10 字段
        if len(fields) != len(PARAM_FIELDS) + 2:
            return None
        data = {}
        try:
            data["request_id"] = int(float(fields[0]))
            data["parameter_revision"] = int(float(fields[1]))
        except ValueError:
            return None
        for name, raw in zip(PARAM_FIELDS, fields[2:]):
            try:
                if name == "control_mode":
                    data[name] = int(float(raw))
                else:
                    data[name] = float(raw)
            except ValueError:
                return None
        return ("paramget", data)

    if kind == "SYNC":
        fields = rest.split(",")
        if len(fields) < 3:
            return None
        try:
            status = int(float(fields[0]))
            seq = int(float(fields[1]))
        except ValueError:
            return None
        reason = ",".join(fields[2:])
        return ("sync", {
            "a_sync_status": status,
            "a_sync_seq": None if seq < 0 else seq,
            "a_sync_reason": reason,
        })

    return ("other", body)


def parse_wind(line):
    """便捷方法：仅解析遥测帧（$WIND/$WIND2），返回 dict 或 None。"""
    r = parse_frame(line)
    if r and r[0] == "wind":
        return r[1]
    return None
