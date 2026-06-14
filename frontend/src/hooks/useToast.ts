import { useState, useCallback, useEffect } from 'react';

export interface Toast {
  id: number;
  message: string;
  type: 'success' | 'error' | 'info';
}

let nextId = 0;

// Module-level listeners so any component can trigger toasts
const listeners = new Set<(t: Toast) => void>();

export function triggerToast(message: string, type: Toast['type'] = 'success') {
  const toast: Toast = { id: ++nextId, message, type };
  for (const fn of listeners) fn(toast);
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((t: Toast) => {
    setToasts((prev) => [...prev, t]);
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== t.id)), 5000);
  }, []);

  useEffect(() => {
    listeners.add(addToast);
    return () => { listeners.delete(addToast); };
  }, [addToast]);

  return toasts;
}
