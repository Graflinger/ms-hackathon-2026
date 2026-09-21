import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";

const EVENT_TYPES = ["status", "result", "message", "tool_call"];

export function useEventRefresh(
  url: string | null,
  queryKey: QueryKey,
  setConnected: Dispatch<SetStateAction<boolean>>,
) {
  const client = useQueryClient();
  const queryKeyRef = useRef(queryKey);
  queryKeyRef.current = queryKey;

  useEffect(() => {
    setConnected(false);
    if (!url || typeof EventSource === "undefined") return;

    const source = new EventSource(url);
    const refresh = () => {
      void client.invalidateQueries({ queryKey: queryKeyRef.current });
    };
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    for (const eventType of EVENT_TYPES) {
      source.addEventListener(eventType, refresh);
    }
    return () => {
      source.close();
      setConnected(false);
    };
  }, [client, setConnected, url]);
}