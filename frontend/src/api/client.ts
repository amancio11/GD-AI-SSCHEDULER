import axios, { type AxiosError } from 'axios';
import { v4 as uuidv4 } from 'uuid';

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Attach a unique request ID to every request for tracing
apiClient.interceptors.request.use((config) => {
  config.headers['X-Request-ID'] = uuidv4();
  return config;
});

// Uniform error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ error?: string; detail?: string; code?: string }>) => {
    const msg =
      error.response?.data?.detail ??
      error.response?.data?.error ??
      error.message ??
      'Errore sconosciuto';
    console.error(`[API] ${error.config?.method?.toUpperCase()} ${error.config?.url} → ${msg}`);
    return Promise.reject(error);
  }
);

export default apiClient;
