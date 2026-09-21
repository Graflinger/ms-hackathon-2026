import { useState, type ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEventRefresh } from "../event-stream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, EventListener>();
  close = vi.fn();

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, listener);
  }

  emit(type: string) {
    this.listeners.get(type)?.(new Event(type));
  }
}

afterEach(() => {
  FakeEventSource.instances = [];
  vi.unstubAllGlobals();
});

describe("event stream refresh", () => {
  it("invalidates named events and falls back after disconnect", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, rerender } = renderHook(
      ({ url }) => {
        const [connected, setConnected] = useState(false);
        useEventRefresh(url, ["project", "run", "one"], setConnected);
        return connected;
      },
      { initialProps: { url: "/events" as string | null }, wrapper },
    );
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/events");

    act(() => source.onopen?.());
    expect(result.current).toBe(true);
    act(() => source.emit("result"));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["project", "run", "one"],
      }),
    );

    act(() => source.onerror?.());
    expect(result.current).toBe(false);
    rerender({ url: null });
    expect(source.close).toHaveBeenCalledOnce();
  });
});