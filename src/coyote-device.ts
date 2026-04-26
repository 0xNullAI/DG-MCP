/**
 * Node-side `DeviceClient` implementation backed by `@stoprocent/noble`.
 *
 * Wraps the protocol adapter from `@dg-kit/protocol` and gives the MCP
 * server a single object with `scan()` / `connect(address)` / `execute(cmd)`
 * / `emergencyStop()` methods.
 */

import type {
  DeviceClient,
  DeviceCommand,
  DeviceCommandResult,
  DeviceState,
} from '@dg-kit/core';
import {
  CoyoteProtocolAdapter,
  V2_DEVICE_NAME_PREFIX,
  V3_DEVICE_NAME_PREFIX,
  V3_SENSOR_NAME_PREFIX,
} from '@dg-kit/protocol';
import noble, { type Peripheral } from '@stoprocent/noble';
import { NobleGATTServer } from './noble-shim.js';

export interface ScanResult {
  address: string;
  name: string;
  rssi: number;
  version: 'v2' | 'v3';
}

export class NobleCoyoteDevice implements DeviceClient {
  private readonly protocol = new CoyoteProtocolAdapter();
  private peripheral: Peripheral | null = null;
  private readonly listeners = new Set<(state: DeviceState) => void>();

  constructor() {
    this.protocol.subscribe((state) => {
      for (const listener of this.listeners) listener(state);
    });
  }

  /**
   * Scan for nearby Coyote devices for `timeoutMs` and return candidates.
   * Filters by name prefix (47L121 / 47L120 = V3, D-LAB ESTIM = V2).
   */
  async scan(timeoutMs = 5_000): Promise<ScanResult[]> {
    await waitForPoweredOn();

    const found = new Map<string, ScanResult>();
    const onDiscover = (p: Peripheral): void => {
      const name = p.advertisement?.localName ?? '';
      const version = classifyName(name);
      if (!version) return;
      if (found.has(p.address)) return;
      found.set(p.address, {
        address: p.address,
        name,
        rssi: p.rssi,
        version,
      });
    };

    noble.on('discover', onDiscover);
    try {
      await noble.startScanningAsync([], false);
      await sleep(timeoutMs);
    } finally {
      noble.removeListener('discover', onDiscover);
      try {
        await noble.stopScanningAsync();
      } catch {
        // ignore
      }
    }

    return [...found.values()];
  }

  /** Connect to a Coyote device by its BLE address (e.g. "AA:BB:..."). */
  async connect(address?: string): Promise<void> {
    if (!address) {
      throw new Error('NobleCoyoteDevice.connect() requires a device address');
    }

    await waitForPoweredOn();

    let peripheral: Peripheral | null = null;
    const onDiscover = (p: Peripheral): void => {
      if (p.address.toLowerCase() === address.toLowerCase()) {
        peripheral = p;
        void noble.stopScanningAsync().catch(() => undefined);
      }
    };

    noble.on('discover', onDiscover);
    try {
      await noble.startScanningAsync([], false);
      const start = Date.now();
      while (!peripheral && Date.now() - start < 10_000) {
        await sleep(100);
      }
    } finally {
      noble.removeListener('discover', onDiscover);
      try {
        await noble.stopScanningAsync();
      } catch {
        // ignore
      }
    }

    if (!peripheral) {
      throw new Error(`Coyote device ${address} not found within 10s`);
    }

    const target: Peripheral = peripheral;
    await target.connectAsync();
    const { services } = await target.discoverAllServicesAndCharacteristicsAsync();

    const server = new NobleGATTServer(target, services);
    const fakeDevice = {
      id: target.address,
      name: target.advertisement?.localName ?? '',
      gatt: {
        connected: target.state === 'connected',
        async connect() {
          return server;
        },
        disconnect: () => {
          void target.disconnectAsync().catch(() => undefined);
        },
      },
      addEventListener: (type: string, handler: ((event: Event) => void) | null): void => {
        if (type === 'gattserverdisconnected' && typeof handler === 'function') {
          target.once('disconnect', () => handler(new Event(type)));
        }
      },
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
    } as unknown as EventTarget & {
      id?: string;
      name?: string;
      gatt?: { connected: boolean; connect(): Promise<unknown>; disconnect(): void };
    };

    this.peripheral = target;
    await this.protocol.onConnected({
      device: fakeDevice as never,
      server: server as never,
    });
  }

  async disconnect(): Promise<void> {
    try {
      await this.protocol.emergencyStop();
      await this.protocol.onDisconnected();
    } finally {
      if (this.peripheral) {
        try {
          await this.peripheral.disconnectAsync();
        } catch {
          // ignore
        }
        this.peripheral = null;
      }
    }
  }

  async getState(): Promise<DeviceState> {
    return this.protocol.getState();
  }

  async execute(command: DeviceCommand): Promise<DeviceCommandResult> {
    return this.protocol.execute(command);
  }

  async emergencyStop(): Promise<void> {
    await this.protocol.emergencyStop();
  }

  onStateChanged(listener: (state: DeviceState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}

function classifyName(name: string): 'v2' | 'v3' | null {
  if (name.startsWith(V3_DEVICE_NAME_PREFIX) || name.startsWith(V3_SENSOR_NAME_PREFIX)) {
    return 'v3';
  }
  if (name.startsWith(V2_DEVICE_NAME_PREFIX)) {
    return 'v2';
  }
  return null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let poweredOnPromise: Promise<void> | null = null;

function waitForPoweredOn(): Promise<void> {
  if (noble.state === 'poweredOn') return Promise.resolve();
  if (poweredOnPromise) return poweredOnPromise;
  poweredOnPromise = new Promise((resolve, reject) => {
    const onChange = (state: string): void => {
      if (state === 'poweredOn') {
        noble.removeListener('stateChange', onChange);
        resolve();
      } else if (state === 'unsupported' || state === 'unauthorized') {
        noble.removeListener('stateChange', onChange);
        reject(new Error(`BLE adapter unavailable: ${state}`));
      }
    };
    noble.on('stateChange', onChange);
  });
  return poweredOnPromise;
}
