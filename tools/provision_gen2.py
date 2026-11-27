#!/usr/bin/env python3
"""BEHA Gen 2 BLE setup using ESP-IDF v5.0.2 provisioning messages.

Defaults to inspection. --apply requires private JSON files for Wi-Fi and broker.
No factory reset, token write, firmware write, or cloud-domain write is performed.
Credentials and full endpoint responses are never printed.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from bleak import BleakClient, BleakScanner

SERVICE = '9097f5e0-abaa-7492-6f4a-63694d6ef6a3'
ENDPOINTS = {name: f'9097{suffix}-abaa-7492-6f4a-63694d6ef6a3' for name, suffix in {
    'prov-session': 'ff50', 'prov-config': 'ff51', 'proto-ver': 'ff53',
    'version': 'ff54', 'device_id': 'ff56', 'custom_data': 'ff5b',
}.items()}


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def integer(number, value):
    return varint(number << 3) + varint(value)


def message(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


def fields(data):
    position = 0

    def read_varint():
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(data):
                raise ValueError('Truncated protobuf')
            byte = data[position]
            position += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('Invalid protobuf varint')

    result = {}
    while position < len(data):
        tag = read_varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError('Invalid protobuf field')
        if wire == 0:
            result[number] = read_varint()
        elif wire == 2:
            size = read_varint()
            end = position + size
            if end > len(data):
                raise ValueError('Truncated protobuf field')
            result[number] = data[position:end]
            position = end
        else:
            raise ValueError('Unsupported protobuf field')
    return result


def session_request():
    # SessionData.sec_ver=2; Sec0Payload.sc=20, not field 1.
    return integer(2, 0) + message(10, message(20, b''))


def config_request(kind, payload=b''):
    # GetStatus=0, SetConfig=2, ApplyConfig=4.
    return integer(1, kind) + message({0: 10, 2: 12, 4: 14}[kind], payload)


def config_response(data, expected_type, payload_field):
    outer = fields(data)
    if outer.get(1, 0) != expected_type or payload_field not in outer:
        raise ValueError('Unexpected Wi-Fi response')
    inner = fields(outer[payload_field])
    if inner.get(1, 0) != 0:
        raise ValueError('Wi-Fi operation rejected')
    return inner


async def exchange(client, uuid, payload):
    label = next((name for name, value in ENDPOINTS.items() if value == uuid), 'mapped endpoint')
    print('Exchanging ' + label, flush=True)
    # CoreBluetooth may negotiate encryption before completing an ATT operation.
    # Short timeouts hide the eventual Insufficient Encryption error.
    await asyncio.wait_for(client.write_gatt_char(uuid, payload, response=True), 35)
    return bytes(await asyncio.wait_for(client.read_gatt_char(uuid), 35))


async def pair_macos(client):
    """Request pairing for this connected peer only, using CoreBluetooth.

    This is an opt-in experiment with private macOS API, not Bleak's public
    pair() method (which macOS does not implement). No other bonds are touched.
    """
    if sys.platform != 'darwin':
        raise ValueError('--pair-macos is only supported on macOS')
    import objc
    from Foundation import NSObject, NSRunLoop, NSDate

    class BehaPairingDelegate(NSObject, protocols=[objc.protocolNamed('CBPairingAgentDelegate')]):
        def pairingAgent_peerDidCompletePairing_(self, agent, peer):
            print('macOS pairing completion callback received.', flush=True)

        def pairingAgent_peerDidFailToCompletePairing_error_(self, agent, peer, error):
            self.failure = (str(error.domain()), int(error.code()))
            print('macOS pairing rejected: ' + self.failure[0] + ' code=' + str(self.failure[1]), flush=True)

        def pairingAgent_peerDidRequestPairing_type_passkey_(self, agent, peer, kind, passkey):
            print('macOS pairing confirmation requested: type=' + str(kind) + ', passkey_present=' + str(passkey is not None), flush=True)

        def pairingAgent_peerDidUnpair_(self, agent, peer):
            print('macOS reported the peer unpaired.', flush=True)

    backend = client._backend
    manager = backend._central_manager_delegate.central_manager
    peer = backend._peripheral
    agent = manager.sharedPairingAgent()
    if agent.isPeerPaired_(peer):
        print('The selected heater already has a Mac pairing record.', flush=True)
        return
    delegate = BehaPairingDelegate.alloc().init()
    delegate.failure = None
    previous_delegate = agent.delegate()
    agent.setDelegate_(delegate)
    print('Requesting native macOS pairing for the selected heater only.', flush=True)
    probe = None
    try:
        agent.pairPeer_(peer)
        probe = asyncio.create_task(client.read_gatt_char(ENDPOINTS['proto-ver']))
        for _ in range(90):
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))
            await asyncio.sleep(0.5)
            if agent.isPeerPaired_(peer):
                print('Native macOS pairing confirmed.', flush=True)
                return
            if probe.done():
                probe.result()  # Surface ATT errors without printing endpoint data.
                print('Protocol characteristic is readable; continuing with identity checks.', flush=True)
                return
            if delegate.failure:
                raise ConnectionError('Native pairing rejected')
            if not client.is_connected:
                raise ConnectionError('Heater disconnected during pairing')
        raise TimeoutError('Native macOS pairing was not confirmed')
    finally:
        if probe is not None and not probe.done():
            probe.cancel()
            try:
                await probe
            except (asyncio.CancelledError, Exception):
                pass
        agent.setDelegate_(previous_delegate)


async def run(args):
    expected_id = args.device_id.replace(':', '').replace('-', '').lower()
    if not re.fullmatch(r'[0-9a-f]{12}', expected_id):
        raise ValueError('A full 12-digit device ID is required')
    name = 'BEHA_' + expected_id[-6:].upper()
    broker = wifi = None
    if args.apply:
        broker = json.loads(Path(args.broker_file).read_text())
        wifi = json.loads(Path(args.wifi_file).read_text())
        if broker.get('expected_device_id', '').lower() != expected_id:
            raise ValueError('Broker file device ID does not match --device-id')
        uri = broker['broker_uri']
        parsed = urlsplit(uri)
        if parsed.scheme not in ('mqtt', 'mqtts') or not parsed.hostname:
            raise ValueError('Invalid broker URI')
        if len(uri.encode()) >= 199:
            raise ValueError('Broker URI too long')
        if not 0 < len(wifi['ssid'].encode()) <= 32:
            raise ValueError('Invalid SSID length')
        if not 8 <= len(wifi['password'].encode()) <= 63:
            raise ValueError('Invalid WPA passphrase length')

    print('Waiting for heater (' + name + ')...', flush=True)
    device = await BleakScanner.find_device_by_filter(
        lambda d, a: (a.local_name or d.name or '').upper() == name,
        timeout=args.scan_seconds)
    if device is None:
        raise TimeoutError('Heater was not found; no settings written')
    print('Heater advertisement found; connecting.', flush=True)
    async with BleakClient(device, timeout=20) as client:
        print('BLE connected; resolving endpoints.', flush=True)
        if args.pair_macos:
            await pair_macos(client)
        endpoints = dict(ENDPOINTS)
        service = client.services.get_service(SERVICE)
        if service is None:
            raise ValueError('Expected BEHA service absent')
        for char in service.characteristics:
            for descriptor in char.descriptors:
                if descriptor.uuid.lower().startswith('00002901-'):
                    try:
                        value = await asyncio.wait_for(client.read_gatt_descriptor(descriptor.handle), 3)
                        name = bytes(value).decode().strip('\0')
                        if name in endpoints:
                            endpoints[name] = char.uuid
                    except (Exception,):
                        pass

        proto = json.loads(await exchange(client, endpoints['proto-ver'], b'?'))
        provision = proto.get('prov', {})
        if provision.get('sec_ver', 0) != 0 or 'no_sec' not in provision.get('cap', []):
            raise ValueError('Unexpected provisioning security; stopping without settings writes')
        response = fields(await exchange(client, endpoints['prov-session'], session_request()))
        if response.get(2, 0) != 0 or 10 not in response:
            raise ValueError('Unexpected session response')
        sec0 = fields(response[10])
        if sec0.get(1, 0) != 1 or 21 not in sec0 or fields(sec0[21]).get(1, 0) != 0:
            raise ValueError('Session was not accepted')
        identity = json.loads(await exchange(client, endpoints['device_id'], b'?'))
        if str(identity.get('device_id', '')).replace(':', '').lower() != expected_id:
            raise ValueError('Device identity does not match --device-id')
        version = json.loads(await exchange(client, endpoints['version'], b'?'))
        print('Heater identity verified; firmware: ' + str(version.get('version')), flush=True)
        if not args.apply:
            print('Inspection complete; no settings written.', flush=True)
            return

        # Stage Wi-Fi before changing the broker. Apply only after broker acknowledgement.
        config = message(1, wifi['ssid'].encode()) + message(2, wifi['password'].encode())
        config_response(await exchange(client, endpoints['prov-config'], config_request(2, config)), 3, 13)
        try:
            payload = json.dumps({'iothub_override': uri}, separators=(',', ':')).encode()
            acknowledgement = json.loads(await exchange(client, endpoints['custom_data'], payload))
            if acknowledgement.get('iothub_override') != uri:
                raise ValueError('Broker override was not acknowledged')
            print('Broker override acknowledged by heater; checking Wi-Fi connection.', flush=True)
            config_response(await exchange(client, endpoints['prov-config'], config_request(4)), 5, 15)
            for _ in range(15):
                await asyncio.sleep(2)
                status = config_response(await exchange(client, endpoints['prov-config'], config_request(0)), 1, 11)
                if status.get(2, 0) == 0:
                    print('Heater reports Wi-Fi connected. Broker traffic and restart persistence still need verification.', flush=True)
                    return
                if status.get(2) == 3:
                    raise ValueError('Heater reports Wi-Fi connection failure')
            raise TimeoutError('Wi-Fi connection did not complete')
        except Exception:
            # A failed acknowledgement can follow a successful NVS write. Do not
            # blindly clear an unknown previous broker setting or assume recovery.
            print('Setup incomplete. Check Wi-Fi/broker state before retrying; settings may have been written.', flush=True)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-id', required=True, help='Full heater Wi-Fi MAC / DeviceDrive ID')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--pair-macos', action='store_true', help='Experimentally request native pairing for the selected heater before GATT access')
    parser.add_argument('--scan-seconds', type=float, default=120)
    parser.add_argument('--wifi-file', help='Private JSON containing ssid and password')
    parser.add_argument('--broker-file', help='Private JSON containing broker_uri and expected_device_id')
    args = parser.parse_args()
    if args.apply and (not args.wifi_file or not args.broker_file):
        parser.error('--apply requires --wifi-file and --broker-file')
    if not 0 < args.scan_seconds <= 300:
        parser.error('--scan-seconds must be greater than 0 and at most 300')
    try:
        asyncio.run(run(args))
    except Exception as exc:
        # Library exceptions can contain raw endpoint bytes; do not log them.
        reason = type(exc).__name__
        if type(exc).__name__ == 'BleakGATTProtocolError' and getattr(exc, 'code', None) == 15:
            reason = 'BLE link encryption required (ATT error 15)'
        print('Setup stopped: ' + reason + '. No success is assumed; verify heater state.', flush=True)
        raise SystemExit(1)
