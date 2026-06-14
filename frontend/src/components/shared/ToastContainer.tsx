import { useToast, type Toast } from '../../hooks/useToast';
import { X } from 'lucide-react';

const TOAST_BG: Record<Toast['type'], string> = {
  success: 'bg-green-600',
  error:   'bg-destructive',
  info:    'bg-primary',
};

export default function ToastContainer() {
  const toasts = useToast();

  if (!toasts.length) return null;

  return (
    <div className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-center gap-2 px-4 py-2.5 rounded-lg shadow-lg text-white text-sm max-w-xs
            pointer-events-auto ${TOAST_BG[t.type]} animate-in fade-in slide-in-from-bottom-4`}
        >
          <span className="flex-1">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
