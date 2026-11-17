#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 O.S. Systems Software LTDA.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end CoAPS/DTLS test of the Zephyr UpdateHub client.

Builds the upstream sample with dtls.conf and runs it against a local
DTLS 1.2 CoAP server (the pion/dtls stand-in, or UpdateHub cdn via
--server-cmd) backed by a fake UpdateHub HTTP API. It checks that:

  * the handshake verifies the server name and a chain to either root in the
    device's bundle (dev R1, or the standby dev R2; see make-test-pki.sh),
  * probe, report and a block-wise download complete and the image is
    verified in flash (byte for byte in slot1 on native_sim),
  * a server certificate from another root, for another host name, or past
    its validity period is rejected by the device before any CoAP request.

Targets:
  native_sim/native/64  host sockets (NSOS); functional and negative tests
  qemu_x86              32-bit, full Zephyr IP stack over QEMU slirp; heap
                        and thread stack measurements

Run it through run-e2e.sh, which provides the toolchain.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

E2E = Path(__file__).resolve().parent.parent
WS = Path(os.environ.get("E2E_WORKSPACE", E2E / "ws")).resolve()
SAMPLE = WS / "zephyr" / "samples" / "subsys" / "mgmt" / "updatehub"
# v4.5-branch still uses the name main dropped in 15ee1e487d8.
DTLS_CONF = "dtls.conf" if (SAMPLE / "dtls.conf").exists() else "overlay-dtls.conf"
BUILD = E2E / os.environ.get("E2E_BUILD", "build")
CONF = E2E / "conf"
PRODUCT = "e4d37cfe6ec48a2d069cc0bbb8b078677e9a0d8df3a027c4d8ea131130c4265f"
BACKEND = "127.0.0.1:8080"
SLOT1_OFFSET = 0x75000
SECTOR_SIZE = 0x1000
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# mbedtls/x509.h MBEDTLS_X509_BADCERT_*
BADCERT_EXPIRED = 0x01
BADCERT_CN_MISMATCH = 0x04
BADCERT_NOT_TRUSTED = 0x08

NEGATIVE = {
    "wrong-root": BADCERT_NOT_TRUSTED,
    "wrong-host": BADCERT_CN_MISMATCH,
    "expired": BADCERT_EXPIRED,
}


class Proc:
    """A child process whose output is captured to a log and searchable."""

    def __init__(self, name, argv, logdir, stdin=False, cwd=None):
        self.name = name
        self.lines = []
        self.cond = threading.Condition()
        self.log = open(logdir / f"{name}.log", "w")
        self.p = subprocess.Popen(
            [str(a) for a in argv],
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
            cwd=cwd,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for raw in self.p.stdout:
            line = ANSI.sub("", raw).rstrip("\r\n")
            self.log.write(line + "\n")
            self.log.flush()
            with self.cond:
                self.lines.append(line)
                self.cond.notify_all()

    def wait_for(self, pattern, timeout, start=0):
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        with self.cond:
            idx = start
            while True:
                while idx < len(self.lines):
                    m = rx.search(self.lines[idx])
                    if m:
                        return m, idx + 1
                    idx += 1
                left = deadline - time.monotonic()
                if left <= 0 or (self.p.poll() is not None and idx >= len(self.lines)):
                    return None, idx
                self.cond.wait(min(left, 0.2))

    def grep(self, pattern, start=0):
        rx = re.compile(pattern)
        with self.cond:
            return [l for l in self.lines[start:] if rx.search(l)]

    def mark(self):
        with self.cond:
            return len(self.lines)

    def send(self, text):
        self.p.stdin.write(text + "\n")
        self.p.stdin.flush()

    def stop(self):
        if self.p.poll() is None:
            os.killpg(self.p.pid, signal.SIGTERM)
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.p.pid, signal.SIGKILL)
                self.p.wait()
        self.log.close()


def run(cmd, **kw):
    print("+", " ".join(shlex.quote(str(c)) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


class Target:
    def __init__(self, name, board, confs, extra=(), modules=(), image="update.bin",
                 server_cert="server", slot1_check=True):
        self.name = name
        self.board = board
        self.confs = confs
        self.extra = list(extra)
        self.modules = modules
        self.image = image
        self.server_cert = server_cert
        self.slot1_check = slot1_check
        self.builddir = BUILD / name

    def build(self, common_extra):
        conf = ";".join([DTLS_CONF] + [str(c) for c in self.confs])
        extra = [f"-DEXTRA_CONF_FILE={conf}", *common_extra, *self.extra]
        if self.modules:
            extra.append("-DEXTRA_ZEPHYR_MODULES=" + ";".join(str(m) for m in self.modules))
        run([sys.executable, "-m", "west", "build", "-p", "-b", self.board, "-d", self.builddir,
             SAMPLE, "--", *extra], cwd=WS)

    def config(self):
        return (self.builddir / "zephyr" / ".config").read_text()

    def start(self, env, logdir):
        if self.board.startswith("native_sim"):
            flash = logdir / "flash.bin"
            shutil.copy(env.out / "img" / "flash.bin", flash)
            exe = self.builddir / "zephyr" / "zephyr.exe"
            return Proc("device", [exe, f"--flash={flash}"], logdir, stdin=True), flash
        return Proc("device", ["ninja", "-C", self.builddir, "run"], logdir, stdin=True), None


class Env:
    def __init__(self, args):
        self.args = args
        self.out = E2E / "run"
        self.logs = self.out / "logs"
        self.bin = E2E / "bin"

    def cert(self, name):
        return self.out / "testpki" / f"{name}.pem", self.out / "testpki" / f"{name}.key"

    def server(self, logdir, cert_name, package):
        cert, key = self.cert(cert_name)
        backend = Proc(
            "backend",
            [self.bin / "fake-backend", "--listen", BACKEND, "--product", PRODUCT,
             "--package", package],
            logdir,
        )
        if self.args.server_cmd:
            cmd = self.args.server_cmd.format(cert=cert, key=key, backend=f"http://{BACKEND}")
            front = Proc("server", shlex.split(cmd), logdir)
            time.sleep(self.args.server_start_delay)
        else:
            front = Proc(
                "server",
                [self.bin / "coaps-standin", "--listen", "127.0.0.1:5684",
                 "--cert", cert, "--key", key, "--backend", f"http://{BACKEND}"],
                logdir,
            )
            if not front.wait_for(r"listening", 10)[0]:
                raise RuntimeError("server did not start")
        return backend, front


def stop_all(*procs):
    for p in procs:
        p.stop()


def scenario_standby_root(env, target):
    """The device trusts both bundled roots: a server on the standby root works."""
    logdir = env.logs / "native_sim-standby-root"
    logdir.mkdir(parents=True, exist_ok=True)
    backend, server = env.server(logdir, "standby", logdir / "none")
    dev, _ = target.start(env, logdir)
    res = {"target": target.board, "pass": False}
    try:
        m, _ = dev.wait_for(r"No update available|Cannot connect to UDP remote", 60)
        res["probe"] = m.group(0) if m else "timeout"
        res["reached_backend"] = bool(backend.grep(r"PROBE"))
        res["pass"] = bool(m and "No update" in m.group(0) and res["reached_backend"])
    finally:
        stop_all(dev, server, backend)
    return res


def scenario_positive(env, target, name, measure):
    logdir = env.logs / name
    logdir.mkdir(parents=True, exist_ok=True)
    package = logdir / "published.bin"
    package.unlink(missing_ok=True)
    image = (env.out / "img" / target.image).read_bytes()
    backend, server = env.server(logdir, target.server_cert, package)
    dev, flash = target.start(env, logdir)
    res = {"target": target.board, "pass": False}
    try:
        m, pos = dev.wait_for(r"No update available", 60)
        if not m:
            raise AssertionError("first probe did not report 'No update available'")
        if not env.args.server_cmd:
            res["suite"] = (server.grep(r"suite=\S+") or ["?"])[0].split("suite=")[-1]

        shutil.copy(env.out / "img" / target.image, package)
        dev.send("updatehub run")
        m, pos = dev.wait_for(
            r"Image flashed successfully|Error installing update|Invalid response", 300, pos)
        if not m or "successfully" not in m.group(0):
            raise AssertionError(f"update failed: {m.group(0) if m else 'timeout'}")
        res["image_bytes"] = len(image)

        if flash and target.slot1_check:
            offset = SLOT1_OFFSET
            # swap-using-offset stores the secondary image one sector into slot1
            if "CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_USING_OFFSET=y" in target.config():
                offset += SECTOR_SIZE
            with open(flash, "rb") as f:
                f.seek(offset)
                res["slot1_matches"] = f.read(len(image)) == image
            if not res["slot1_matches"]:
                raise AssertionError("slot1 does not hold the published image")

        if not env.args.server_cmd:
            res["handshakes"] = len(server.grep(r"HANDSHAKE OK"))
        res["download_requests"] = len(backend.grep(r"DOWNLOAD /products/"))
        res["reports"] = [json.loads(l.split("REPORT ", 1)[1])["status"]
                          for l in backend.grep(r"REPORT \{")]
        sizes = [int(x) for l in dev.grep(r"f_recv")
                 for x in re.findall(r"f_recv\(_timeout\)\(\) returned (\d+)", l)]
        if sizes:
            res["max_dtls_datagram_rx"] = max(sizes)

        if measure:
            mark = dev.mark()
            dev.send("mbedtls heap")
            m, _ = dev.wait_for(r"Maximum \(peak\): (\d+) bytes", 10, mark)
            res["mbedtls_heap_peak"] = int(m.group(1)) if m else None
            res["mbedtls_heap_size"] = int(re.search(r"CONFIG_MBEDTLS_HEAP_SIZE=(\d+)",
                                                     target.config()).group(1))
            mark = dev.mark()
            dev.send("kernel thread stacks")
            dev.wait_for(r"IRQ 00", 10, mark)
            time.sleep(0.5)
            stacks = {}
            for l in dev.lines[mark:]:
                sm = re.search(r"0x[0-9a-f]+ (\S+)\s+\(real size\s*(\d+)\):\s+unused\s+(\d+)"
                               r"\s+usage\s+(\d+)", l)
                if sm:
                    stacks[sm.group(1)] = {"size": int(sm.group(2)), "used": int(sm.group(4))}
            if stacks and all(s["used"] == stacks[next(iter(stacks))]["used"] for s in stacks.values()):
                stacks = "not measurable on this target"
            res["stacks"] = stacks
        res["pass"] = True
    except AssertionError as e:
        res["error"] = str(e)
    finally:
        stop_all(dev, server, backend)
    return res


def scenario_negative(env, target, case, flag):
    logdir = env.logs / f"negative-{case}"
    logdir.mkdir(parents=True, exist_ok=True)
    backend, server = env.server(logdir, case, logdir / "none")
    dev, _ = target.start(env, logdir)
    res = {"pass": False, "expected_flags": f"0x{flag:08x}"}
    try:
        m, _ = dev.wait_for(r"Certificate verification flags ([0-9a-f]+)", 60)
        dev.wait_for(r"Cannot connect to UDP remote", 10)
        time.sleep(1)
        res["flags"] = f"0x{m.group(1)}" if m else None
        if not env.args.server_cmd:
            res["server_alert"] = next(iter(server.grep(r"handshake failed")), "") \
                .split("handshake error: ")[-1]
            res["handshake_completed"] = bool(server.grep(r"HANDSHAKE OK"))
        res["reached_backend"] = bool(backend.grep(r"PROBE"))
        res["pass"] = bool(m and (int(m.group(1), 16) & flag)
                           and not res.get("handshake_completed")
                           and not res["reached_backend"])
    finally:
        stop_all(dev, server, backend)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true", help="reuse existing builds")
    ap.add_argument("--skip-qemu", action="store_true", help="native_sim only")
    ap.add_argument("--server-cmd",
                    help="run this instead of the stand-in on 127.0.0.1:5684; "
                         "{cert} {key} {backend} are substituted")
    ap.add_argument("--server-start-delay", type=float, default=3.0)
    ap.add_argument("--block-size-exp", type=int, default=6,
                    help="CONFIG_UPDATEHUB_COAP_BLOCK_SIZE_EXP")
    args = ap.parse_args()

    env = Env(args)
    if env.logs.exists():
        shutil.rmtree(env.logs)
    env.logs.mkdir(parents=True)

    if shutil.which("go"):
        run(["go", "build", "-o", f"{env.bin}/", "./cmd/..."], cwd=E2E / "server")
    run([E2E / "scripts/make-images.sh", env.out / "img"])
    run([E2E / "scripts/make-test-pki.sh", env.out / "testpki"])

    common_extra = [
        f"-DCONFIG_UPDATEHUB_COAP_BLOCK_SIZE_EXP={args.block_size_exp}",
        f'-DCONFIG_UPDATEHUB_SAMPLE_ROOT_CA_FILE="{env.out / "testpki" / "dev-roots.pem"}"',
    ]
    native = [CONF / "e2e.conf", CONF / "native_sim.conf"]
    targets = {
        "native": Target("native_sim", "native_sim/native/64", native + [CONF / "measure.conf"]),
        "native-debug": Target("native_sim-debug", "native_sim/native/64",
                               native + [CONF / "tls-debug.conf"]),
    }
    if not args.skip_qemu:
        targets["qemu"] = Target(
            "qemu_x86", "qemu_x86",
            [CONF / "e2e.conf", CONF / "measure.conf", CONF / "qemu_x86.conf"],
            modules=[E2E / "harness" / "slot0-header"],
            image="update-32k.bin", server_cert="qemu-host", slot1_check=False,
        )
    if not args.no_build:
        for t in targets.values():
            t.build(common_extra)

    results = {
        "native_sim": scenario_positive(env, targets["native"], "native_sim", measure=True),
        "native_sim-tls-debug": scenario_positive(env, targets["native-debug"],
                                                  "native_sim-tls-debug", measure=False),
    }
    results["native_sim-standby-root"] = scenario_standby_root(env, targets["native-debug"])
    for case, flag in NEGATIVE.items():
        results[f"negative-{case}"] = scenario_negative(env, targets["native-debug"], case, flag)
    if "qemu" in targets:
        results["qemu_x86"] = scenario_positive(env, targets["qemu"], "qemu_x86", measure=True)

    (env.out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    ok = all(r["pass"] for r in results.values())
    print("E2E", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
