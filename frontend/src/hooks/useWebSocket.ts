import { useEffect, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useUiStore } from '../store/uiStore';
import { useAiStore } from '../store/aiStore';

const WS_BASE = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
const MAX_RETRIES = 10;
const BASE_DELAY_MS = 1_000;

interface WsMessage {
  type: 'RESCHEDULE_COMPLETE' | 'AI_SUGGESTION_NEW' | 'SCHEDULE_INFEASIBLE' | string;
  scenario_id?: string;
  makespan_days?: number;
  count?: number;
  conflicts?: string[];
}

export function useWebSocket(roomId: string | null | undefined) {
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const queryClient = useQueryClient();
  const setWsConnected = useUiStore((s) => s.setWebsocketConnected);
  const incrementUnread = useAiStore((s) => s.incrementUnread);

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      let msg: WsMessage;
      try {
        msg = JSON.parse(event.data as string) as WsMessage;
      } catch {
        return;
      }

      switch (msg.type) {
        case 'RESCHEDULE_COMPLETE':
          if (msg.scenario_id) {
            queryClient.invalidateQueries({ queryKey: ['schedule', msg.scenario_id] });
            queryClient.invalidateQueries({ queryKey: ['gantt', msg.scenario_id] });
            queryClient.invalidateQueries({ queryKey: ['scenario', msg.scenario_id] });
          }
          break;

        case 'AI_SUGGESTION_NEW':
          incrementUnread(msg.count ?? 1);
          if (msg.scenario_id) {
            queryClient.invalidateQueries({ queryKey: ['ai-suggestions', msg.scenario_id] });
          }
          break;

        case 'SCHEDULE_INFEASIBLE':
          console.warn('[WS] SCHEDULE_INFEASIBLE', msg.conflicts);
          break;

        default:
          break;
      }
    },
    [queryClient, incrementUnread]
  );

  const connect = useCallback(() => {
    if (!roomId) return;

    const url = `${WS_BASE}/ws/${roomId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retriesRef.current = 0;
      setWsConnected(true);
    };

    ws.onmessage = handleMessage;

    ws.onclose = () => {
      setWsConnected(false);
      wsRef.current = null;

      if (retriesRef.current < MAX_RETRIES) {
        const delay = Math.min(BASE_DELAY_MS * 2 ** retriesRef.current, 30_000);
        retriesRef.current += 1;
        timeoutRef.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [roomId, handleMessage, setWsConnected]);

  useEffect(() => {
    connect();
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      wsRef.current?.close();
      setWsConnected(false);
    };
  }, [connect, setWsConnected]);

  const send = useCallback((data: WsMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { send };
}
