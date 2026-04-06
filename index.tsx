import React, { useState, useRef, useEffect, useMemo } from "react";
import { createRoot } from "react-dom/client";
import { GoogleGenAI } from "@google/genai";
import {
  Scale,
  AlertTriangle,
  MessageSquare,
  FileText,
  Search,
  Upload,
  CheckCircle,
  XCircle,
  Gavel,
  BookOpen,
  Send,
  Loader2,
  Trash2,
  Database,
  LayoutDashboard,
  Filter,
  CheckSquare,
  Square,
  PieChart,
  Settings,
  Menu,
  Cloud,
  Wifi,
  RefreshCw,
  FolderInput,
  X,
  Eye,
  ChevronDown,
  ChevronUp,
  GraduationCap,
  Download,
  Pencil,
  Save,
  Bookmark,
  BarChart3,
  ChevronRight,
  Package,
  Layers,
  TrendingUp,
  Smartphone,
  Share2,
  Plus,
  Shield,
  Copy,
  LogOut,
  Lock,
  UserCircle,
  Target,
  ClipboardCheck,
  Files,
  FolderOpen,
  Bell,
  MessageCircle,
  Clock,
  Archive,
  Briefcase,
} from "lucide-react";

// --- Types ---

type AppMode = 'dashboard' | 'datalake' | 'spete' | 'drafter' | 'redflags' | 'chat' | 'clarification' | 'rag' | 'training' | 'analytics' | 'strategy' | 'compliance' | 'multi_document' | 'dosare' | 'alerts' | 'settings' | 'profile' | 'pricing';

interface AuthUser {
  id: string;
  email: string;
  nume: string | null;
  rol: string;
  activ: boolean;
  email_verified: boolean;
  created_at: string | null;
  queries_today: number;
  queries_limit: number;
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
}

interface LLMModelInfo {
  id: string;
  input_tokens: number;
  output_tokens: number;
}

interface LLMProviderInfo {
  configured: boolean;
  models: LLMModelInfo[];
}

interface LLMSettingsData {
  active_provider: string;
  active_model: string | null;
  providers: Record<string, LLMProviderInfo>;
}

interface UploadedFile {
  id: string;
  name: string;
  type: string;
  content: string; // Base64
  isActive: boolean; // Controls if file is sent to Context
  metadata?: {
    year?: string;
    bulletin?: string;
    critics?: string[];
    cpv?: string;
    ruling?: 'Admis' | 'Respins' | 'Unknown';
    sourcePath?: string;
  };
}

interface Message {
  role: 'user' | 'model';
  text: string;
}

// --- Helper Functions ---

const generateId = () => Math.random().toString(36).substr(2, 9);

const fetchStream = async (
  url: string,
  body: any,
  onChunk: (text: string) => void,
  onDone: (meta: any) => void,
  onError: (error: string) => void,
  onStatus?: (status: string) => void,
) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const errBody = await response.json();
      const d = errBody.detail;
      if (typeof d === 'string') detail = d;
      else if (Array.isArray(d)) detail = d.map((e: any) => e.msg || JSON.stringify(e)).join('; ');
      else if (d) detail = JSON.stringify(d);
      else detail = JSON.stringify(errBody);
    } catch { /* ignore parse errors */ }
    onError(detail);
    return;
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop()!;
    for (const part of parts) {
      if (part.startsWith('data: ')) {
        try {
          const data = JSON.parse(part.slice(6));
          if (data.error) { onError(typeof data.error === 'string' ? data.error : JSON.stringify(data.error)); return; }
          if (data.status && onStatus) onStatus(data.status);
          if (data.text) onChunk(data.text);
          if (data.done) onDone(data);
        } catch { /* ignore malformed SSE */ }
      }
    }
  }
};

const parseFilenameMetadata = (filename: string) => {
  // Expected: BO2025 - [Bulletin] - [Critics] - [CPV] - [A/R].txt/pdf
  const metadata: UploadedFile['metadata'] = { ruling: 'Unknown' };

  if (filename.includes('BO2025') || filename.includes('BO2024')) {
    metadata.year = filename.substring(2, 6);
  }

  const nameWithoutExt = filename.split('.')[0];
  if (nameWithoutExt.endsWith('A') || nameWithoutExt.endsWith(' A')) metadata.ruling = 'Admis';
  if (nameWithoutExt.endsWith('R') || nameWithoutExt.endsWith(' R')) metadata.ruling = 'Respins';

  return metadata;
};

const fileToBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => {
      const result = reader.result as string;
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = error => reject(error);
  });
};

// --- Auth Utilities ---

const storeTokens = (access: string, refresh: string) => {
  localStorage.setItem('expertap_access_token', access);
  localStorage.setItem('expertap_refresh_token', refresh);
};
const clearTokens = () => {
  localStorage.removeItem('expertap_access_token');
  localStorage.removeItem('expertap_refresh_token');
};
const getAccessToken = () => localStorage.getItem('expertap_access_token');
const getRefreshToken = () => localStorage.getItem('expertap_refresh_token');

const authFetch = async (url: string, options: RequestInit = {}): Promise<Response> => {
  const token = getAccessToken();
  const headers = new Headers(options.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (!headers.has('Content-Type') && options.body && typeof options.body === 'string') {
    headers.set('Content-Type', 'application/json');
  }
  let response = await fetch(url, { ...options, headers });

  // Auto-refresh on 401
  if (response.status === 401 && getRefreshToken()) {
    const refreshResponse = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: getRefreshToken() }),
    });
    if (refreshResponse.ok) {
      const data = await refreshResponse.json();
      storeTokens(data.access_token, data.refresh_token);
      headers.set('Authorization', `Bearer ${data.access_token}`);
      response = await fetch(url, { ...options, headers });
    } else {
      clearTokens();
    }
  }
  return response;
};

const authFetchStream = async (
  url: string,
  body: any,
  onChunk: (text: string) => void,
  onDone: (meta: any) => void,
  onError: (error: string) => void,
  onStatus?: (status: string) => void,
) => {
  let token = getAccessToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });

  // Auto-refresh on 401
  if (response.status === 401 && getRefreshToken()) {
    const refreshResponse = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: getRefreshToken() }),
    });
    if (refreshResponse.ok) {
      const data = await refreshResponse.json();
      storeTokens(data.access_token, data.refresh_token);
      token = data.access_token;
      headers['Authorization'] = `Bearer ${token}`;
      response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      });
    } else {
      clearTokens();
    }
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const errBody = await response.json();
      const d = errBody.detail;
      if (typeof d === 'string') detail = d;
      else if (Array.isArray(d)) detail = d.map((e: any) => e.msg || JSON.stringify(e)).join('; ');
      else if (d && d.message) detail = d.message;
      else if (d) detail = JSON.stringify(d);
      else detail = JSON.stringify(errBody);
    } catch { /* ignore parse errors */ }
    onError(detail);
    return;
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop()!;
    for (const part of parts) {
      if (part.startsWith('data: ')) {
        try {
          const data = JSON.parse(part.slice(6));
          if (data.error) { onError(typeof data.error === 'string' ? data.error : JSON.stringify(data.error)); return; }
          if (data.status && onStatus) onStatus(data.status);
          if (data.text) onChunk(data.text);
          if (data.done) onDone(data);
        } catch { /* ignore malformed SSE */ }
      }
    }
  }
};

// Feature access rules per role
const ROLE_FEATURES: Record<string, string[]> = {
  registered: ['chat', 'dashboard', 'datalake', 'spete', 'rag', 'analytics', 'profile', 'pricing'],
  paid_basic: ['chat', 'dashboard', 'datalake', 'spete', 'rag', 'analytics', 'strategy', 'compliance', 'drafter', 'redflags', 'clarification', 'dosare', 'alerts', 'profile', 'pricing'],
  paid_pro: ['chat', 'dashboard', 'datalake', 'spete', 'rag', 'analytics', 'strategy', 'compliance', 'multi_document', 'drafter', 'redflags', 'clarification', 'training', 'export', 'dosare', 'alerts', 'profile', 'pricing'],
  paid_enterprise: ['chat', 'dashboard', 'datalake', 'spete', 'rag', 'analytics', 'strategy', 'compliance', 'multi_document', 'drafter', 'redflags', 'clarification', 'training', 'export', 'dosare', 'alerts', 'profile', 'pricing'],
  admin: ['chat', 'dashboard', 'datalake', 'spete', 'rag', 'analytics', 'strategy', 'compliance', 'multi_document', 'drafter', 'redflags', 'clarification', 'training', 'export', 'dosare', 'alerts', 'settings', 'profile', 'pricing'],
};

const PLAN_LABELS: Record<string, string> = {
  registered: 'Free',
  paid_basic: 'Basic',
  paid_pro: 'Pro',
  paid_enterprise: 'Enterprise',
  admin: 'Admin',
};

// --- Markdown Formatter ---

// Map heading keywords to emoji
const headingEmoji = (title: string): string => {
  const t = title.toLowerCase();
  if (t.includes('enun')) return '📋';
  if (t.includes('cerin')) return '✅';
  if (t.includes('rezolv')) return '⚖️';
  if (t.includes('note') && t.includes('trainer')) return '🎓';
  if (t.includes('scenariu')) return '🎭';
  if (t.includes('context')) return 'ℹ️';
  if (t.includes('argumen') || t.includes('pro') || t.includes('contra')) return '💬';
  if (t.includes('concluzi') || t.includes('concluz')) return '🏁';
  if (t.includes('răspuns') || t.includes('soluți') || t.includes('varianta corect')) return '✔️';
  if (t.includes('întrebare') || t.includes('quiz')) return '❓';
  if (t.includes('rol')) return '👥';
  if (t.includes('erori') || t.includes('greșel')) return '⚠️';
  if (t.includes('cronolog') || t.includes('pași') || t.includes('etap')) return '📅';
  if (t.includes('compar')) return '🔀';
  if (t.includes('legisl') || t.includes('legal') || t.includes('temei')) return '⚖️';
  if (t.includes('jurispruden')) return '🏛️';
  return '';
};

const formatMarkdown = (text: string): string => {
  return text
    // Escape HTML
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Citation links: [[BO2025_1000]] -> clickable pill tag
    .replace(/\[\[(BO\d{4}_\d+)\]\]/g, '<a href="#" data-decision="$1" onclick="window.__openDecision && window.__openDecision(\'$1\'); return false;" style="display:inline-flex;align-items:center;background:#eff6ff;color:#1d4ed8;padding:2px 10px;border-radius:9999px;border:1px solid #bfdbfe;font-family:monospace;font-size:0.8em;font-weight:600;cursor:pointer;text-decoration:none;margin:2px 3px;transition:background 0.15s" onmouseover="this.style.background=\'#dbeafe\'" onmouseout="this.style.background=\'#eff6ff\'">$1</a>')
    // ANAP spete: [[ANAP_speta_123]] -> clickable teal pill
    .replace(/\[\[ANAP_speta_(\d+)\]\]/g, '<a href="#" data-speta="$1" onclick="window.__openSpeta && window.__openSpeta($1); return false;" style="display:inline-flex;align-items:center;background:#f0fdfa;color:#0d9488;padding:2px 10px;border-radius:9999px;border:1px solid #99f6e4;font-family:monospace;font-size:0.8em;font-weight:600;cursor:pointer;text-decoration:none;margin:2px 3px;transition:background 0.15s" onmouseover="this.style.background=\'#ccfbf1\'" onmouseout="this.style.background=\'#f0fdfa\'">Speța ANAP $1</a>')
    // Bold: **text**
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic: *text*
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Headers with emoji
    .replace(/^### (.+)$/gm, (_m, title) => {
      const em = headingEmoji(title);
      return `<h3 style="font-size:1rem;font-weight:700;margin:1rem 0 0.4rem 0;color:#1e293b">${em ? em + ' ' : ''}${title}</h3>`;
    })
    .replace(/^## (.+)$/gm, (_m, title) => {
      const em = headingEmoji(title);
      return `<h2 style="font-size:1.1rem;font-weight:700;margin:1.2rem 0 0.4rem 0;color:#1e293b">${em ? em + ' ' : ''}${title}</h2>`;
    })
    .replace(/^# (.+)$/gm, (_m, title) => {
      const em = headingEmoji(title);
      return `<h1 style="font-size:1.25rem;font-weight:700;margin:1.2rem 0 0.4rem 0;color:#1e293b">${em ? em + ' ' : ''}${title}</h1>`;
    })
    // Numbered lists: 1. item — compact spacing
    .replace(/^(\d+)\.\s+(.+)$/gm, '<div style="display:flex;gap:0.5rem;margin:0.1rem 0 0.1rem 1rem;line-height:1.5"><span style="color:#64748b;min-width:1.2rem;font-weight:600">$1.</span><span>$2</span></div>')
    // Bullet lists: - item — compact spacing with FA icon
    .replace(/^[-•]\s+(.+)$/gm, '<div style="display:flex;gap:0.5rem;margin:0.1rem 0 0.1rem 1rem;line-height:1.5"><span style="color:#d97706;font-size:0.6rem;margin-top:0.5rem">◆</span><span>$1</span></div>')
    // Horizontal rules
    .replace(/^---$/gm, '<hr style="border:none;border-top:1px solid #e2e8f0;margin:1rem 0"/>')
    // Paragraphs: double newlines
    .replace(/\n\n/g, '</p><p style="margin:0.75rem 0">')
    // Single newlines within paragraphs
    .replace(/\n/g, '<br/>')
    // Wrap in paragraph
    .replace(/^/, '<p style="margin:0 0 0.5rem 0">')
    .replace(/$/, '</p>');
};

// Character counter for text inputs
const CharCounter = ({ value, maxLength }: { value: string; maxLength: number }) => {
  const len = value.length;
  const words = value.trim() ? value.trim().split(/\s+/).length : 0;
  const pct = len / maxLength;
  const color = pct > 1 ? 'text-red-600 font-bold' : pct > 0.9 ? 'text-amber-600' : 'text-slate-400';
  return (
    <div className={`text-xs mt-1 flex justify-end gap-2 ${color}`}>
      <span>{words} cuv.</span>
      <span>{len.toLocaleString()} / {maxLength.toLocaleString()} car.{pct > 1 ? ' — limită depășită!' : ''}</span>
    </div>
  );
};

// Critique codes legend (CNSC standard)
const CRITIQUE_LEGEND: Record<string, string> = {
  // Documentație (D)
  D1: "Cerințe restrictive — experiență similară, calificare, specificații tehnice",
  D2: "Criterii de atribuire / factori de evaluare netransparenți sau subiectivi",
  D3: 'Denumiri de produse/mărci fără sintagma \u201Esau echivalent\u201D',
  D4: "Lipsa răspuns clar la solicitările de clarificări",
  D5: "Forma de constituire a garanției de participare",
  D6: "Clauze contractuale inechitabile sau excesive",
  D7: "Nedivizarea achiziției pe loturi",
  D8: "Alte critici documentație",
  DAL: "Alte critici documentație",
  // Rezultat (R)
  R1: "Contestații proces-verbal ședință deschidere oferte",
  R2: "Respingerea ofertei ca neconformă sau inacceptabilă",
  R3: "Prețul neobișnuit de scăzut al altor ofertanți",
  R4: "Documente calificare alți ofertanți / mod de evaluare",
  R5: "Lipsa precizării motivelor de respingere",
  R6: "Lipsa solicitare clarificări / apreciere incorectă răspunsuri",
  R7: "Anularea fără temei legal a procedurii",
  R8: "Alte critici rezultat",
  RAL: "Alte critici rezultat",
};

// --- Components ---

// Inline SVG logo — always renders, no external file dependency
const AppLogo = ({ size = 36, className = "" }: { size?: number; className?: string }) => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none" width={size} height={size} className={className}>
    <defs>
      <linearGradient id="logo-bg" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stopColor="#2563EB"/>
        <stop offset="100%" stopColor="#7C3AED"/>
      </linearGradient>
    </defs>
    <rect width="64" height="64" rx="14" fill="url(#logo-bg)"/>
    <g stroke="white" strokeLinecap="round" strokeLinejoin="round" fill="none">
      <line x1="32" y1="12" x2="32" y2="48" strokeWidth="3.5"/>
      <line x1="22" y1="48" x2="42" y2="48" strokeWidth="3.5"/>
      <line x1="12" y1="17" x2="52" y2="15" strokeWidth="3.5"/>
      <line x1="12" y1="17" x2="10" y2="30" strokeWidth="2"/>
      <line x1="12" y1="17" x2="14" y2="30" strokeWidth="2"/>
      <path d="M8,30 Q12,37 16,30" strokeWidth="2.5"/>
      <line x1="52" y1="15" x2="50" y2="26" strokeWidth="2"/>
      <line x1="52" y1="15" x2="54" y2="26" strokeWidth="2"/>
      <path d="M48,26 Q52,33 56,26" strokeWidth="2.5"/>
    </g>
    <rect x="24" y="38" width="3" height="8" rx="1" fill="white" opacity="0.35"/>
    <rect x="29" y="35" width="3" height="11" rx="1" fill="white" opacity="0.35"/>
    <rect x="34" y="37" width="3" height="9" rx="1" fill="white" opacity="0.35"/>
    <rect x="39" y="33" width="3" height="13" rx="1" fill="white" opacity="0.35"/>
  </svg>
);

const SidebarItem = ({ 
  icon: Icon, 
  label, 
  active, 
  onClick,
  badge
}: { 
  icon: any, 
  label: string, 
  active: boolean, 
  onClick: () => void,
  badge?: number
}) => (
  <button 
    onClick={onClick}
    className={`w-full flex items-center gap-3 px-4 py-3 text-sm font-medium transition-all rounded-md mb-1 ${
      active 
        ? "bg-blue-600 text-white shadow-md" 
        : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
    }`}
  >
    <Icon size={18} />
    <span className="flex-1 text-left">{label}</span>
    {badge !== undefined && badge > 0 && (
      <span className="bg-blue-500/20 text-blue-200 text-xs px-2 py-0.5 rounded-full">{badge}</span>
    )}
  </button>
);

const HistoryPanel = ({ items, loading, type, onLoad, onDelete, onClose }: {
  items: any[], loading: boolean, type: string,
  onLoad: (item: any) => void, onDelete: (id: string) => void, onClose: () => void
}) => (
  <div className="fixed right-0 top-0 w-80 h-full bg-white border-l border-slate-200 shadow-xl z-40 flex flex-col">
    <div className="flex justify-between items-center p-4 border-b border-slate-100">
      <h3 className="font-bold text-slate-800 text-sm">Istoric</h3>
      <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
    </div>
    <div className="flex-1 overflow-y-auto p-3 space-y-2">
      {loading ? (
        <div className="flex justify-center py-8"><Loader2 className="animate-spin text-slate-400" size={24} /></div>
      ) : items.length === 0 ? (
        <p className="text-center text-slate-400 text-sm py-8">Niciun element salvat</p>
      ) : items.map((item: any) => (
        <div key={item.id} className="group bg-slate-50 rounded-lg p-3 hover:bg-slate-100 transition cursor-pointer" onClick={() => onLoad(item)}>
          <div className="flex justify-between items-start">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-800 truncate">{item.titlu || item.tema || item.titlu}</p>
              <p className="text-xs text-slate-400 mt-1">{new Date(item.created_at).toLocaleDateString('ro-RO', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}</p>
              {item.numar_mesaje !== undefined && <p className="text-xs text-slate-500 mt-0.5">{item.numar_mesaje} mesaje</p>}
              {item.total_flags !== undefined && <p className="text-xs text-slate-500 mt-0.5">{item.total_flags} flags ({item.critice} critice)</p>}
              {item.tip_material && <p className="text-xs text-slate-500 mt-0.5">{item.tip_material} — {item.nivel_dificultate}</p>}
            </div>
            <button onClick={(e) => { e.stopPropagation(); onDelete(item.id); }} className="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-600 p-1 transition"><Trash2 size={14} /></button>
          </div>
        </div>
      ))}
    </div>
  </div>
);

const StatCard = ({ label, value, icon: Icon, color }: { label: string, value: string | number, icon: any, color: string }) => (
  <div className="bg-white p-6 rounded-xl border border-slate-100 shadow-sm flex items-center gap-4">
    <div className={`p-4 rounded-full ${color} bg-opacity-10`}>
      <Icon className={color.replace('bg-', 'text-')} size={24} />
    </div>
    <div>
      <p className="text-slate-500 text-sm font-medium">{label}</p>
      <p className="text-2xl font-bold text-slate-800">{value}</p>
    </div>
  </div>
);

// --- Main Application ---

const App = () => {
  const [mode, setMode] = useState<AppMode>('chat');
  const [apiKey] = useState(process.env.API_KEY || "");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [fileSearch, setFileSearch] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [apiDecisions, setApiDecisions] = useState<any[]>([]);
  const [apiDecisionsTotal, setApiDecisionsTotal] = useState(0);
  const [apiDecisionsPage, setApiDecisionsPage] = useState(1);
  const [isLoadingDecisions, setIsLoadingDecisions] = useState(false);
  const [dbStats, setDbStats] = useState<any>(null);
  const [filterRuling, setFilterRuling] = useState<string[]>([]);
  const [filterType, setFilterType] = useState("");
  const [filterYears, setFilterYears] = useState<string[]>([]);
  const [filterCritici, setFilterCritici] = useState<string[]>([]);
  const [filterCpv, setFilterCpv] = useState<string[]>([]);
  const [criticiOptions, setCriticiOptions] = useState<{code: string, count: number}[]>([]);
  const [cpvOptions, setCpvOptions] = useState<{code: string, description: string | null, count: number}[]>([]);
  const [cpvSearchTerm, setCpvSearchTerm] = useState("");
  const [filterCategorie, setFilterCategorie] = useState("");
  const [filterClasa, setFilterClasa] = useState("");
  const [categoriiOptions, setCategoriiOptions] = useState<{name: string, count: number}[]>([]);
  const [claseOptions, setClaseOptions] = useState<{name: string, count: number}[]>([]);
  const [cpvTree, setCpvTree] = useState<any[]>([]);
  const [cpvTreeExpanded, setCpvTreeExpanded] = useState<Set<string>>(new Set());
  const [cpvTopStats, setCpvTopStats] = useState<{code: string, description: string | null, categorie: string | null, count: number}[]>([]);
  const [categoriiStats, setCategoriiStats] = useState<{name: string, count: number}[]>([]);
  const [winRateByCategory, setWinRateByCategory] = useState<any[]>([]);
  const [winRateByCritici, setWinRateByCritici] = useState<any[]>([]);
  const [cpvTopGrouped, setCpvTopGrouped] = useState<any[]>([]);
  const [showInstallBanner, setShowInstallBanner] = useState(() => {
    try { return localStorage.getItem('expertap-install-dismissed') !== '1'; } catch { return true; }
  });
  const [showRulingDropdown, setShowRulingDropdown] = useState(false);
  const [showCriticiDropdown, setShowCriticiDropdown] = useState(false);
  const [showCpvDropdown, setShowCpvDropdown] = useState(false);
  const [showCategorieDropdown, setShowCategorieDropdown] = useState(false);
  const [showClasaDropdown, setShowClasaDropdown] = useState(false);
  const [filterMotivRespingere, setFilterMotivRespingere] = useState<string[]>([]);
  const [filterComplet, setFilterComplet] = useState<string[]>([]);
  const [filterDomeniu, setFilterDomeniu] = useState<string[]>([]);
  const [filterTipProcedura, setFilterTipProcedura] = useState<string[]>([]);
  const [filterCriteriuAtribuire, setFilterCriteriuAtribuire] = useState<string[]>([]);
  const [filterDateFrom, setFilterDateFrom] = useState("");
  const [filterDateTo, setFilterDateTo] = useState("");
  const [filterValoareMin, setFilterValoareMin] = useState("");
  const [filterValoareMax, setFilterValoareMax] = useState("");
  const [motivRespingereOptions, setMotivRespingereOptions] = useState<{name: string, count: number}[]>([]);
  const [completOptions, setCompletOptions] = useState<{name: string, count: number}[]>([]);
  const [domeniuOptions, setDomeniuOptions] = useState<{name: string, count: number}[]>([]);
  const [tipProceduraOptions, setTipProceduraOptions] = useState<{name: string, count: number}[]>([]);
  const [criteriuAtribuireOptions, setCriteriuAtribuireOptions] = useState<{name: string, count: number}[]>([]);
  const [showMotivDropdown, setShowMotivDropdown] = useState(false);
  const [showCompletDropdown, setShowCompletDropdown] = useState(false);
  const [showDomeniuDropdown, setShowDomeniuDropdown] = useState(false);
  const [showTipProceduraDropdown, setShowTipProceduraDropdown] = useState(false);
  const [showCriteriuAtribuireDropdown, setShowCriteriuAtribuireDropdown] = useState(false);
  const [decisionViewTab, setDecisionViewTab] = useState<'raw' | 'analysis'>('raw');
  const [decisionAnalysis, setDecisionAnalysis] = useState<any>(null);
  const [isLoadingAnalysis, setIsLoadingAnalysis] = useState(false);

  // Chat/Interaction States
  const [chatMessages, setChatMessages] = useState<Message[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [streamStatus, setStreamStatus] = useState("");
  const [generatedContent, setGeneratedContent] = useState<string>("");
  const [generatedDecisionRefs, setGeneratedDecisionRefs] = useState<string[]>([]);

  // Specialized Input States
  const [drafterContext, setDrafterContext] = useState({ facts: "", authorityArgs: "", legalGrounds: "", remediiSolicitate: "", detaliiProcedura: "", numarDecizieCnsc: "" });
  const [drafterDocType, setDrafterDocType] = useState<'contestatie' | 'plangere'>('contestatie');

  // Analytics States
  const [analyticsTab, setAnalyticsTab] = useState<'panels' | 'predictor' | 'compare'>('panels');
  const [panelsList, setPanelsList] = useState<any[]>([]);
  const [selectedPanel, setSelectedPanel] = useState<string | null>(null);
  const [panelProfile, setPanelProfile] = useState<any>(null);
  const [panelsLoading, setPanelsLoading] = useState(false);
  const [predictorInput, setPredictorInput] = useState({ coduri_critici: [] as string[], cod_cpv: '', complet: '', tip_procedura: '', tip_contestatie: '' });
  const [analyticsFilterOptions, setAnalyticsFilterOptions] = useState<{critici: any[], complete: any[], proceduri: any[]}>({critici: [], complete: [], proceduri: []});
  const [cpvPredictorSearch, setCpvPredictorSearch] = useState('');
  const [cpvPredictorResults, setCpvPredictorResults] = useState<any[]>([]);
  const [predictorResult, setPredictorResult] = useState<any>(null);
  const [predictorLoading, setPredictorLoading] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>(['', '']);
  const [compareResult, setCompareResult] = useState<any>(null);
  const [compareLoading, setCompareLoading] = useState(false);

  // Strategy Generator
  const [strategyInput, setStrategyInput] = useState({ description: '', coduri_critici: [] as string[], cod_cpv: '', complet: '', tip_procedura: '', tip_contestatie: '', valoare_estimata: '' });
  const [strategyResult, setStrategyResult] = useState<any>(null);
  const [strategyLoading, setStrategyLoading] = useState(false);

  // Compliance Checker
  const [complianceText, setComplianceText] = useState('');
  const [complianceFile, setComplianceFile] = useState<File | null>(null);
  const [complianceResult, setComplianceResult] = useState<any>(null);
  const [complianceLoading, setComplianceLoading] = useState(false);
  const [complianceProcedura, setComplianceProcedura] = useState('');

  // Multi-Document Analysis
  const [multiDocFiles, setMultiDocFiles] = useState<File[]>([]);
  const [multiDocResult, setMultiDocResult] = useState<any>(null);
  const [multiDocLoading, setMultiDocLoading] = useState(false);

  // Import States (Data Lake)
  const [showImportModal, setShowImportModal] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [importResult, setImportResult] = useState<any>(null);
  const [editingDecision, setEditingDecision] = useState<any>(null);
  const [editDecisionForm, setEditDecisionForm] = useState<Record<string, string>>({});
  const [clarificationClause, setClarificationClause] = useState("");
  const [memoTopic, setMemoTopic] = useState("");

  // Red Flags States
  const [redFlagsText, setRedFlagsText] = useState("");
  const [redFlagsResults, setRedFlagsResults] = useState<any[]>([]);
  const [redFlagsTab, setRedFlagsTab] = useState<'manual' | 'upload'>('manual');
  const [uploadedDocsDrafter, setUploadedDocsDrafter] = useState<{name: string, text: string}[]>([]);
  const [uploadedDocsClarification, setUploadedDocsClarification] = useState<{name: string, text: string}[]>([]);
  const [uploadedDocsRag, setUploadedDocsRag] = useState<{name: string, text: string}[]>([]);
  const [uploadedDocsRedFlags, setUploadedDocsRedFlags] = useState<{name: string, text: string}[]>([]);
  const [redFlagsProgress, setRedFlagsProgress] = useState("");
  const [selectedRedFlags, setSelectedRedFlags] = useState<number[]>([]);
  const [editedClarifications, setEditedClarifications] = useState<Record<number, string>>({});
  const [editingClarificationIdx, setEditingClarificationIdx] = useState<number | null>(null);

  // Training States
  const [trainingTema, setTrainingTema] = useState("");
  const [trainingTip, setTrainingTip] = useState("speta");
  const [trainingSelectedTypes, setTrainingSelectedTypes] = useState<string[]>(["speta", "quiz"]);
  const [trainingNivel, setTrainingNivel] = useState("mediu");
  const [trainingLungime, setTrainingLungime] = useState("mediu");
  const [trainingContext, setTrainingContext] = useState("");
  const [trainingResult, setTrainingResult] = useState<string>("");
  const [trainingLoading, setTrainingLoading] = useState(false);
  const [trainingActiveTab, setTrainingActiveTab] = useState<'material' | 'rezolvare' | 'note'>('material');
  const [trainingMeta, setTrainingMeta] = useState<any>(null);
  const [trainingShowContext, setTrainingShowContext] = useState(false);
  const [trainingEditing, setTrainingEditing] = useState(false);
  const [trainingEditedResult, setTrainingEditedResult] = useState<string | null>(null);
  const [trainingPublicTinta, setTrainingPublicTinta] = useState("");
  const [trainingMode, setTrainingMode] = useState<'individual' | 'batch' | 'program'>('individual');
  const [trainingBatchCount, setTrainingBatchCount] = useState(3);
  const [trainingBatchCustom, setTrainingBatchCustom] = useState(false);
  const [trainingProgramPlan, setTrainingProgramPlan] = useState("");
  const [uploadedDocsTrainingContext, setUploadedDocsTrainingContext] = useState<{name: string, text: string}[]>([]);
  const [uploadedDocsTrainingPlan, setUploadedDocsTrainingPlan] = useState<{name: string, text: string}[]>([]);
  const [trainingBatchProgress, setTrainingBatchProgress] = useState<{current: number, total: number, results: string[]} | null>(null);

  // Dosare Digitale States
  const [dosare, setDosare] = useState<any[]>([]);
  const [dosareLoading, setDosareLoading] = useState(false);
  const [dosareLoaded, setDosareLoaded] = useState(false);
  const [dosarForm, setDosarForm] = useState({ titlu: '', descriere: '', client: '', autoritate_contractanta: '', numar_dosar: '', numar_procedura: '', cod_cpv: '', valoare_estimata: '', tip_procedura: '', termen_depunere: '', termen_solutionare: '', note: '' });
  const [dosarEditing, setDosarEditing] = useState<string | null>(null);
  const [dosarViewing, setDosarViewing] = useState<any>(null);
  const [dosarStats, setDosarStats] = useState<any>(null);
  const [dosarFilter, setDosarFilter] = useState('');
  const [dosarShowForm, setDosarShowForm] = useState(false);

  // Active Dosar (persistent across pages)
  const [activeDosarId, setActiveDosarId] = useState<string | null>(() => localStorage.getItem('activeDosarId'));
  const [activeDosarInfo, setActiveDosarInfo] = useState<{titlu: string; client?: string; status: string} | null>(null);
  const [activeDosarDocs, setActiveDosarDocs] = useState<{id: string; filename: string; text: string}[]>([]);
  const [dosarDocsLoading, setDosarDocsLoading] = useState(false);

  // Dosar Documents (for detail view)
  const [dosarDocuments, setDosarDocuments] = useState<any[]>([]);
  const [dosarDocUploading, setDosarDocUploading] = useState(false);

  // Alert Rules States
  const [alertRules, setAlertRules] = useState<any[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(false);
  const [alertsLoaded, setAlertsLoaded] = useState(false);
  const [alertForm, setAlertForm] = useState({ nume: '', descriere: '', cod_cpv: '', coduri_critici: '', complet: '', tip_procedura: '', solutie: '', keywords: '', frecventa: 'zilnic' });
  const [alertShowForm, setAlertShowForm] = useState(false);
  const [alertEditing, setAlertEditing] = useState<string | null>(null);

  // Save/History States
  const [saveStatus, setSaveStatus] = useState<{type: 'success' | 'error', text: string} | null>(null);
  const [historyPanel, setHistoryPanel] = useState<string | null>(null); // which history panel is open
  const [historyItems, setHistoryItems] = useState<any[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // LLM Settings States
  const [llmSettings, setLlmSettings] = useState<LLMSettingsData | null>(null);
  const [settingsProvider, setSettingsProvider] = useState("gemini");
  const [settingsModel, setSettingsModel] = useState("");
  const [settingsApiKey, setSettingsApiKey] = useState("");
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsTesting, setSettingsTesting] = useState(false);
  const [settingsTestResult, setSettingsTestResult] = useState<{success: boolean, response_time_ms: number, error?: string} | null>(null);
  const [settingsMessage, setSettingsMessage] = useState<{type: 'success' | 'error', text: string} | null>(null);

  // Decision Viewer State
  const [viewingDecision, setViewingDecision] = useState<any | null>(null);
  const [isLoadingDecision, setIsLoadingDecision] = useState(false);
  const [decisionSearchTerm, setDecisionSearchTerm] = useState("");
  const [decisionSearchIndex, setDecisionSearchIndex] = useState(0);
  const decisionContentRef = useRef<HTMLDivElement>(null);

  // ANAP Speta Viewer State
  const [viewingSpeta, setViewingSpeta] = useState<any>(null);
  const [isLoadingSpeta, setIsLoadingSpeta] = useState(false);

  // ANAP Spete Page State
  const [spete, setSpete] = useState<any[]>([]);
  const [spetePage, setSpetePage] = useState(1);
  const [speteTotal, setSpeteTotal] = useState(0);
  const [spetePages, setSpetePages] = useState(1);
  const [speteCategories, setSpeteCategories] = useState<{categorie: string, count: number}[]>([]);
  const [speteTags, setSpeteTags] = useState<{tag: string, count: number}[]>([]);
  const [speteSearch, setSpeteSearch] = useState('');
  const [speteFilterCat, setSpeteFilterCat] = useState('');
  const [speteFilterTag, setSpeteFilterTag] = useState('');
  const [speteSemantic, setSpeteSemantic] = useState(false);
  const [speteStats, setSpeteStats] = useState<any>(null);
  const [isLoadingSpete, setIsLoadingSpete] = useState(false);

  // RAG Memo save state
  const [ragMemoSaved, setRagMemoSaved] = useState(false);

  // Search Scopes State
  const [scopes, setScopes] = useState<{id: string, name: string, description: string | null, filters: any, decision_count: number}[]>([]);
  const [activeScopeId, setActiveScopeId] = useState<string | null>(null);
  const [enableReranking, setEnableReranking] = useState(false);
  const [enableExpansion, setEnableExpansion] = useState(false);
  const [showScopeModal, setShowScopeModal] = useState(false);
  const [showScopeManager, setShowScopeManager] = useState(false);
  const [editingScope, setEditingScope] = useState<{id: string, name: string, description: string | null} | null>(null);
  const [editingScopeFilters, setEditingScopeFilters] = useState<string | null>(null);
  const [scopeName, setScopeName] = useState("");
  const [scopeDescription, setScopeDescription] = useState("");
  const [showYearDropdown, setShowYearDropdown] = useState(false);

  // Auth State
  const [authState, setAuthState] = useState<AuthState>({ user: null, loading: true });
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [authMode, setAuthMode] = useState<'login' | 'register' | 'forgotPassword' | 'resetPassword'>('login');
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authConfirmPassword, setAuthConfirmPassword] = useState('');
  const [authNume, setAuthNume] = useState('');
  const [authError, setAuthError] = useState('');
  const [authLoading, setAuthLoading] = useState(false);
  const [resetToken, setResetToken] = useState('');
  const [resetNewPassword, setResetNewPassword] = useState('');
  const [resetConfirmPassword, setResetConfirmPassword] = useState('');
  const [forgotPasswordMessage, setForgotPasswordMessage] = useState('');

  // Profile State
  const [profileCurrentPassword, setProfileCurrentPassword] = useState('');
  const [profileNewPassword, setProfileNewPassword] = useState('');
  const [profileConfirmPassword, setProfileConfirmPassword] = useState('');
  const [profileNume, setProfileNume] = useState('');
  const [profileMessage, setProfileMessage] = useState<{type: 'success' | 'error', text: string} | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [verificationCode, setVerificationCode] = useState('');
  const [verificationLoading, setVerificationLoading] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState<{type: 'success' | 'error', text: string} | null>(null);

  // Initialize profile name when user loads or changes
  useEffect(() => {
    if (authState.user?.nume) setProfileNume(authState.user.nume);
  }, [authState.user?.nume]);

  // Mobile Sidebar State
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Auth: check stored token on mount
  useEffect(() => {
    const token = getAccessToken();
    if (token) {
      authFetch('/api/v1/auth/me').then(res => {
        if (res.ok) return res.json();
        clearTokens();
        return null;
      }).then(data => {
        setAuthState({ user: data, loading: false });
      }).catch(() => setAuthState({ user: null, loading: false }));
    } else {
      setAuthState({ user: null, loading: false });
    }
  }, []);

  // Auth helper: canAccess feature
  const canAccess = (feature: string): boolean => {
    const user = authState.user;
    // Unregistered users: only chat
    if (!user) return feature === 'chat';
    const features = ROLE_FEATURES[user.rol];
    if (!features) return false;
    return features.includes(feature);
  };

  // Auth helper: refresh user data after usage
  const refreshAuthUser = async () => {
    if (!getAccessToken()) return;
    try {
      const res = await authFetch('/api/v1/auth/me');
      if (res.ok) {
        const data = await res.json();
        setAuthState(prev => ({ ...prev, user: data }));
      }
    } catch { /* ignore */ }
  };

  // Auth handlers
  const handleLogin = async () => {
    setAuthError('');
    setAuthLoading(true);
    try {
      const formData = new URLSearchParams();
      formData.append('username', authEmail);
      formData.append('password', authPassword);
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData,
      });
      if (!res.ok) {
        try {
          const err = await res.json();
          setAuthError(err.detail || 'Eroare la autentificare');
        } catch {
          setAuthError(`Eroare server (${res.status})`);
        }
        setAuthLoading(false);
        return;
      }
      const data = await res.json();
      storeTokens(data.access_token, data.refresh_token);
      setAuthState({ user: data.user, loading: false });
      setShowAuthModal(false);
      setAuthEmail(''); setAuthPassword('');
    } catch (e: any) {
      setAuthError('Eroare de rețea');
    }
    setAuthLoading(false);
  };

  const handleRegister = async () => {
    setAuthError('');
    if (authPassword !== authConfirmPassword) {
      setAuthError('Parolele nu coincid');
      return;
    }
    if (authPassword.length < 8) {
      setAuthError('Parola trebuie să aibă minim 8 caractere');
      return;
    }
    setAuthLoading(true);
    try {
      const res = await fetch('/api/v1/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: authEmail, password: authPassword, nume: authNume || null }),
      });
      if (!res.ok) {
        const err = await res.json();
        setAuthError(err.detail || 'Eroare la înregistrare');
        setAuthLoading(false);
        return;
      }
      const data = await res.json();
      storeTokens(data.access_token, data.refresh_token);
      setAuthState({ user: data.user, loading: false });
      setShowAuthModal(false);
      setAuthEmail(''); setAuthPassword(''); setAuthConfirmPassword(''); setAuthNume('');
    } catch (e: any) {
      setAuthError('Eroare de rețea');
    }
    setAuthLoading(false);
  };

  const handleForgotPassword = async () => {
    setAuthError('');
    setForgotPasswordMessage('');
    if (!authEmail.trim()) {
      setAuthError('Introdu adresa de email');
      return;
    }
    setAuthLoading(true);
    try {
      const res = await fetch('/api/v1/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: authEmail }),
      });
      if (res.ok) {
        setForgotPasswordMessage('Dacă adresa există în sistem, vei primi un email cu codul de resetare.');
      } else {
        setAuthError('Eroare la trimiterea cererii');
      }
    } catch {
      setAuthError('Eroare de rețea');
    }
    setAuthLoading(false);
  };

  const handleResetPassword = async () => {
    setAuthError('');
    if (!resetToken.trim()) {
      setAuthError('Introdu codul de resetare din email');
      return;
    }
    if (resetNewPassword.length < 8) {
      setAuthError('Parola trebuie să aibă minim 8 caractere');
      return;
    }
    if (resetNewPassword !== resetConfirmPassword) {
      setAuthError('Parolele nu coincid');
      return;
    }
    setAuthLoading(true);
    try {
      const res = await fetch('/api/v1/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, new_password: resetNewPassword }),
      });
      if (res.ok) {
        setAuthMode('login');
        setAuthError('');
        setResetToken(''); setResetNewPassword(''); setResetConfirmPassword('');
        setForgotPasswordMessage('Parola a fost resetată cu succes! Autentifică-te cu noua parolă.');
      } else {
        const err = await res.json();
        setAuthError(err.detail || 'Eroare la resetarea parolei');
      }
    } catch {
      setAuthError('Eroare de rețea');
    }
    setAuthLoading(false);
  };

  const handleLogout = () => {
    clearTokens();
    setAuthState({ user: null, loading: false });
  };

  // Global click handler to close all dropdowns
  useEffect(() => {
    const handleGlobalClick = () => {
      setShowRulingDropdown(false);
      setShowYearDropdown(false);
      setShowCriticiDropdown(false);
      setShowCpvDropdown(false);
    };
    document.addEventListener('click', handleGlobalClick);
    return () => document.removeEventListener('click', handleGlobalClick);
  }, []);

  const chatEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const ai = new GoogleGenAI({ apiKey });

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, generatedContent]);

  // Register global handlers for citation clicks
  useEffect(() => {
    (window as any).__openDecision = (decisionId: string) => {
      openDecision(decisionId);
    };
    (window as any).__openSpeta = (numarSpeta: number) => {
      openSpeta(numarSpeta);
    };
    return () => {
      delete (window as any).__openDecision;
      delete (window as any).__openSpeta;
    };
  }, []);

  // Scroll to active search match in decision modal
  useEffect(() => {
    if (!decisionContentRef.current || !decisionSearchTerm || decisionSearchTerm.length < 2) return;
    const timer = setTimeout(() => {
      const marks = decisionContentRef.current?.querySelectorAll('mark[data-match]');
      if (marks && marks.length > 0 && decisionSearchIndex < marks.length) {
        marks[decisionSearchIndex]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 50);
    return () => clearTimeout(timer);
  }, [decisionSearchIndex, decisionSearchTerm]);

  // Reset search index when search term changes
  useEffect(() => {
    setDecisionSearchIndex(0);
  }, [decisionSearchTerm]);

  useEffect(() => {
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);
  }, [mode]);

  // Fetch global stats from API on mount
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await authFetch('/api/v1/decisions/stats/overview');
        if (response.ok) {
          const data = await response.json();
          setDbStats(data);
        }
      } catch (error) {
        console.error('Failed to fetch stats:', error);
      }
    };
    fetchStats();
  }, []);

  // Fetch LLM settings on mount
  const fetchLLMSettings = async () => {
    try {
      const response = await authFetch('/api/v1/settings/llm');
      if (response.ok) {
        const data: LLMSettingsData = await response.json();
        setLlmSettings(data);
        setSettingsProvider(data.active_provider);
        setSettingsModel(data.active_model || '');
      }
    } catch (error) {
      console.error('Failed to fetch LLM settings:', error);
    }
  };

  useEffect(() => {
    if (authState.user?.rol === 'admin') fetchLLMSettings();
  }, [authState.user?.rol]);

  // Fetch search scopes
  const fetchScopes = async () => {
    try {
      const res = await authFetch('/api/v1/scopes/');
      if (res.ok) setScopes(await res.json());
    } catch (e) { console.error('Failed to fetch scopes:', e); }
  };

  useEffect(() => {
    fetchScopes();
  }, [authState.user?.id]);

  const deleteScope = async (id: string) => {
    try {
      const res = await authFetch(`/api/v1/scopes/${id}`, { method: 'DELETE' });
      if (res.ok) {
        if (activeScopeId === id) setActiveScopeId(null);
        await fetchScopes();
      }
    } catch (e) { console.error('Failed to delete scope:', e); }
  };

  const updateScope = async (id: string, name: string, description: string | null, filters?: any) => {
    try {
      const body: any = { name, description };
      if (filters) body.filters = filters;
      const res = await authFetch(`/api/v1/scopes/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setEditingScope(null);
        await fetchScopes();
      }
    } catch (e) { console.error('Failed to update scope:', e); }
  };

  // Reusable scope selector component
  const ScopeSelector = ({ compact = false }: { compact?: boolean }) => (
    <div className={`flex items-center gap-2 ${compact ? '' : 'mb-3'}`}>
      <Filter size={compact ? 12 : 14} className="text-slate-400 shrink-0" />
      <select
        value={activeScopeId || ''}
        onChange={(e) => setActiveScopeId(e.target.value || null)}
        className={`border border-slate-200 rounded-lg px-2 py-1.5 bg-white text-slate-600 focus:ring-2 focus:ring-blue-500/40 outline-none transition cursor-pointer ${compact ? 'text-[11px] max-w-[200px]' : 'text-xs flex-1 max-w-xs'}`}
      >
        <option value="">Toate deciziile</option>
        {scopes.map(s => (
          <option key={s.id} value={s.id}>{s.name} ({s.decision_count})</option>
        ))}
      </select>
      {scopes.length > 0 && (
        <button onClick={() => setShowScopeManager(true)} className="text-[10px] text-slate-400 hover:text-blue-600 transition whitespace-nowrap">
          Gestionează
        </button>
      )}
    </div>
  );

  // Active scope indicator pill
  const ActiveScopeIndicator = () => {
    const scope = scopes.find(s => s.id === activeScopeId);
    if (!scope) return null;
    return (
      <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-1.5 mb-3">
        <Filter size={13} className="text-blue-600 shrink-0" />
        <span className="text-xs text-blue-700 font-medium">
          Căutare restricționată la: <strong>"{scope.name}"</strong> ({scope.decision_count} decizii)
        </span>
        <button onClick={() => setActiveScopeId(null)} className="ml-auto text-blue-400 hover:text-blue-700 shrink-0"><X size={14} /></button>
      </div>
    );
  };

  const handleSaveSettings = async () => {
    setSettingsSaving(true);
    setSettingsMessage(null);
    try {
      const body: any = {
        active_provider: settingsProvider,
        active_model: settingsModel || null,
      };
      if (settingsApiKey.trim()) {
        body.api_key = settingsApiKey.trim();
      }
      const response = await authFetch('/api/v1/settings/llm', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (response.ok) {
        const data: LLMSettingsData = await response.json();
        setLlmSettings(data);
        setSettingsApiKey('');
        setSettingsMessage({ type: 'success', text: 'Setări salvate cu succes!' });
      } else {
        const err = await response.json();
        setSettingsMessage({ type: 'error', text: err.detail || 'Eroare la salvare' });
      }
    } catch (error) {
      setSettingsMessage({ type: 'error', text: 'Eroare de rețea' });
    } finally {
      setSettingsSaving(false);
    }
  };

  const handleTestConnection = async () => {
    setSettingsTesting(true);
    setSettingsTestResult(null);
    try {
      const response = await authFetch('/api/v1/settings/llm/test', { method: 'POST' });
      if (response.ok) {
        const data = await response.json();
        setSettingsTestResult(data);
      }
    } catch (error) {
      setSettingsTestResult({ success: false, response_time_ms: 0, error: 'Eroare de rețea' });
    } finally {
      setSettingsTesting(false);
    }
  };

  // Fetch decisions for Data Lake (paginated + search)
  const fetchDecisions = async (page: number = 1, search?: string) => {
    setIsLoadingDecisions(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: '20' });
      if (search && search.trim()) {
        params.set('search', search.trim());
      }
      if (filterRuling.length > 0) params.set('ruling', filterRuling.join(','));
      if (filterType) params.set('tip_contestatie', filterType);
      if (filterYears.length > 0) params.set('years', filterYears.join(','));
      if (filterCritici.length > 0) params.set('coduri_critici', filterCritici.join(','));
      if (filterCpv.length > 0) params.set('cpv_codes', filterCpv.join(','));
      if (filterCategorie) params.set('categorie', filterCategorie);
      if (filterClasa) params.set('clasa', filterClasa);
      if (filterMotivRespingere.length > 0) params.set('motiv_respingere', filterMotivRespingere.join(','));
      if (filterComplet.length > 0) params.set('complet', filterComplet.join(','));
      if (filterDomeniu.length > 0) params.set('domeniu_legislativ', filterDomeniu.join(','));
      if (filterTipProcedura.length > 0) params.set('tip_procedura', filterTipProcedura.join(','));
      if (filterCriteriuAtribuire.length > 0) params.set('criteriu_atribuire', filterCriteriuAtribuire.join(','));
      if (filterDateFrom) params.set('data_decizie_from', filterDateFrom);
      if (filterDateTo) params.set('data_decizie_to', filterDateTo);
      if (filterValoareMin) params.set('valoare_min', filterValoareMin);
      if (filterValoareMax) params.set('valoare_max', filterValoareMax);
      const response = await fetch(`/api/v1/decisions/?${params}`);
      if (response.ok) {
        const data = await response.json();
        setApiDecisions(data.decisions || []);
        setApiDecisionsTotal(data.total || 0);
        setApiDecisionsPage(data.page || 1);
      }
    } catch (error) {
      console.error('Failed to fetch decisions:', error);
    } finally {
      setIsLoadingDecisions(false);
    }
  };

  // Fetch filter options (critique codes + CPV codes + categories)
  const fetchFilterOptions = async () => {
    try {
      const [critRes, cpvRes, catRes, motivRes, completRes, domeniuRes, procRes, criteriumRes] = await Promise.all([
        authFetch('/api/v1/decisions/filters/critici-codes'),
        authFetch('/api/v1/decisions/filters/cpv-codes'),
        authFetch('/api/v1/decisions/filters/categorii'),
        authFetch('/api/v1/decisions/filters/motiv-respingere'),
        authFetch('/api/v1/decisions/filters/complete'),
        authFetch('/api/v1/decisions/filters/domenii'),
        authFetch('/api/v1/decisions/filters/tipuri-procedura'),
        authFetch('/api/v1/decisions/filters/criterii-atribuire'),
      ]);
      if (critRes.ok) setCriticiOptions(await critRes.json());
      if (cpvRes.ok) setCpvOptions(await cpvRes.json());
      if (catRes.ok) setCategoriiOptions(await catRes.json());
      if (motivRes.ok) setMotivRespingereOptions(await motivRes.json());
      if (completRes.ok) setCompletOptions(await completRes.json());
      if (domeniuRes.ok) setDomeniuOptions(await domeniuRes.json());
      if (procRes.ok) setTipProceduraOptions(await procRes.json());
      if (criteriumRes.ok) setCriteriuAtribuireOptions(await criteriumRes.json());
    } catch (e) { console.error('Failed to fetch filter options:', e); }
  };

  useEffect(() => {
    fetchDecisions(1);
    fetchFilterOptions();
  }, []);

  // Debounced search for Data Lake (triggers on search or filter change)
  useEffect(() => {
    const timer = setTimeout(() => {
      fetchDecisions(1, fileSearch);
    }, 300);
    return () => clearTimeout(timer);
  }, [fileSearch, filterRuling, filterType, filterYears, filterCritici, filterCpv, filterCategorie, filterClasa, filterMotivRespingere, filterComplet, filterDomeniu, filterTipProcedura, filterCriteriuAtribuire, filterDateFrom, filterDateTo, filterValoareMin, filterValoareMax]);

  // Fetch product classes when category changes
  useEffect(() => {
    const fetchClase = async () => {
      try {
        const url = filterCategorie
          ? `/api/v1/decisions/filters/clase?categorie=${encodeURIComponent(filterCategorie)}`
          : '/api/v1/decisions/filters/clase';
        const res = await fetch(url);
        if (res.ok) setClaseOptions(await res.json());
      } catch (e) { /* ignore */ }
    };
    fetchClase();
  }, [filterCategorie]);

  // Fetch CPV tree data
  useEffect(() => {
    const fetchTree = async () => {
      try {
        const url = filterCategorie
          ? `/api/v1/decisions/filters/cpv-tree?categorie=${encodeURIComponent(filterCategorie)}`
          : '/api/v1/decisions/filters/cpv-tree';
        const res = await fetch(url);
        if (res.ok) setCpvTree(await res.json());
      } catch (e) { /* ignore */ }
    };
    fetchTree();
  }, [filterCategorie]);

  // Fetch dashboard CPV stats + enriched win-rate data
  useEffect(() => {
    const fetchCpvStats = async () => {
      try {
        const [topRes, catRes, wrCatRes, wrCritRes, cpvGroupRes] = await Promise.all([
          authFetch('/api/v1/decisions/stats/cpv-top?limit=10'),
          authFetch('/api/v1/decisions/stats/categorii'),
          authFetch('/api/v1/decisions/stats/win-rate-by-category'),
          authFetch('/api/v1/decisions/stats/win-rate-by-critici'),
          authFetch('/api/v1/decisions/stats/cpv-top-grouped?limit=15'),
        ]);
        if (topRes.ok) setCpvTopStats(await topRes.json());
        if (catRes.ok) setCategoriiStats(await catRes.json());
        if (wrCatRes.ok) setWinRateByCategory(await wrCatRes.json());
        if (wrCritRes.ok) setWinRateByCritici(await wrCritRes.json());
        if (cpvGroupRes.ok) setCpvTopGrouped(await cpvGroupRes.json());
      } catch (e) { /* ignore */ }
    };
    fetchCpvStats();
  }, []);

  // Debounced CPV search for dropdown filtering (refetch default when cleared)
  useEffect(() => {
    const timer = setTimeout(async () => {
      try {
        const url = cpvSearchTerm.trim()
          ? `/api/v1/decisions/filters/cpv-codes?search=${encodeURIComponent(cpvSearchTerm)}`
          : `/api/v1/decisions/filters/cpv-codes`;
        const res = await fetch(url);
        if (res.ok) setCpvOptions(await res.json());
      } catch (e) { /* ignore */ }
    }, 300);
    return () => clearTimeout(timer);
  }, [cpvSearchTerm]);

  // --- File Management ---

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setIsUploading(true);
      const newFiles: UploadedFile[] = [];
      // Limit batch size to prevent freezing UI on 3000 files
      const MAX_BATCH = 100; 
      
      const fileList = Array.from(e.target.files);
      
      // Process in chunks if needed, but for now simple iteration
      for (let i = 0; i < fileList.length; i++) {
        const file = fileList[i];
        
        // Skip large files to prevent crash
        if (file.size > 5 * 1024 * 1024) continue;

        try {
          const base64 = await fileToBase64(file);
          newFiles.push({
            id: generateId(),
            name: file.name,
            type: file.type,
            content: base64,
            isActive: i < 10, // Only activate first 10 to save tokens
            metadata: {
              ...parseFilenameMetadata(file.name),
              sourcePath: (file as any).webkitRelativePath || "upload"
            }
          });
        } catch (err) {
          console.error("Error reading file", file.name, err);
        }
      }
      
      setFiles(prev => [...prev, ...newFiles]);
      setIsUploading(false);
    }
  };

  const removeFile = (id: string) => {
    setFiles(files.filter(f => f.id !== id));
  };

  const toggleFileActive = (id: string) => {
    setFiles(files.map(f => f.id === id ? { ...f, isActive: !f.isActive } : f));
  };

  const toggleAllActive = (active: boolean) => {
    const visibleFiles = files.filter(f => f.name.toLowerCase().includes(fileSearch.toLowerCase()));
    const visibleIds = new Set(visibleFiles.map(f => f.id));
    setFiles(files.map(f => visibleIds.has(f.id) ? { ...f, isActive: active } : f));
  };

  const activeFiles = useMemo(() => files.filter(f => f.isActive), [files]);
  const filteredFiles = useMemo(() => files.filter(f => f.name.toLowerCase().includes(fileSearch.toLowerCase())), [files, fileSearch]);

  const simulateSync = () => {
    setIsSyncing(true);
    setTimeout(() => {
      setIsSyncing(false);
      // Trigger file dialog
      if (fileInputRef.current) {
        fileInputRef.current.click();
      }
    }, 1500);
  }

  // --- Decision Viewer ---

  const openDecision = async (decisionId: string, tab: 'raw' | 'analysis' = 'raw') => {
    setIsLoadingDecision(true);
    setDecisionViewTab(tab);
    setDecisionAnalysis(null);
    try {
      const response = await fetch(`/api/v1/decisions/${encodeURIComponent(decisionId)}`);
      if (response.ok) {
        const data = await response.json();
        setViewingDecision(data);
        // If analysis tab requested, also fetch analysis
        if (tab === 'analysis') {
          fetchDecisionAnalysis(decisionId);
        }
      } else {
        alert('Nu s-a putut încărca decizia.');
      }
    } catch (error) {
      console.error('Failed to fetch decision:', error);
      alert('Eroare la încărcarea deciziei.');
    } finally {
      setIsLoadingDecision(false);
    }
  };

  const openSpeta = async (numarSpeta: number) => {
    setIsLoadingSpeta(true);
    try {
      const res = await authFetch(`/api/v1/spete/${numarSpeta}`);
      if (res.ok) {
        setViewingSpeta(await res.json());
      } else {
        alert('Nu s-a putut încărca speța ANAP.');
      }
    } catch (error) {
      console.error('Failed to fetch speta:', error);
      alert('Eroare la încărcarea speței.');
    } finally {
      setIsLoadingSpeta(false);
    }
  };

  const fetchDecisionAnalysis = async (decisionId: string) => {
    setIsLoadingAnalysis(true);
    try {
      const res = await fetch(`/api/v1/decisions/${encodeURIComponent(decisionId)}/analysis`);
      if (res.ok) {
        setDecisionAnalysis(await res.json());
      }
    } catch (e) {
      console.error('Failed to fetch analysis:', e);
    } finally {
      setIsLoadingAnalysis(false);
    }
  };

  // --- API Interaction Handlers ---

  const getActiveContextParts = () => {
    return activeFiles.map(f => ({
      inlineData: { mimeType: f.type || 'text/plain', data: f.content }
    }));
  };

  // === SAVE & HISTORY HELPERS ===

  const showSaveToast = (success: boolean, msg?: string) => {
    setSaveStatus({ type: success ? 'success' : 'error', text: msg || (success ? 'Salvat cu succes!' : 'Eroare la salvare') });
    setTimeout(() => setSaveStatus(null), 3000);
  };

  const saveConversation = async () => {
    if (chatMessages.length === 0) return;
    try {
      const firstUserMsg = chatMessages.find(m => m.role === 'user')?.text || 'Conversație';
      const res = await authFetch('/api/v1/saved/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          titlu: firstUserMsg.slice(0, 200),
          mesaje: chatMessages.map((m, i) => ({ rol: m.role, continut: m.text, citations: (m as any).citations || [], ordine: i })),
          scope_id: activeScopeId || null,
          dosar_id: activeDosarId || null,
        }),
      });
      showSaveToast(res.ok);
    } catch { showSaveToast(false); }
  };

  const saveDocument = async (tipDocument: string, titlu: string, continut: string, referinte: string[] = [], meta: any = {}) => {
    try {
      const res = await authFetch('/api/v1/saved/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tip_document: tipDocument, titlu, continut, referinte_decizii: referinte, metadata: meta, dosar_id: activeDosarId || null }),
      });
      showSaveToast(res.ok);
    } catch { showSaveToast(false); }
  };

  const saveRedFlags = async () => {
    if (redFlagsResults.length === 0) return;
    const critice = redFlagsResults.filter(r => r.severity === 'CRITICĂ').length;
    const medii = redFlagsResults.filter(r => r.severity === 'MEDIE').length;
    const scazute = redFlagsResults.filter(r => r.severity === 'SCĂZUTĂ').length;
    try {
      const res = await authFetch('/api/v1/saved/redflags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          titlu: `Analiză Red Flags — ${redFlagsResults.length} flags`,
          text_analizat_preview: redFlagsText.slice(0, 500),
          rezultate: redFlagsResults.map((rf, i) => ({ ...rf, edited_clarification: editedClarifications[i] || null })),
          total_flags: redFlagsResults.length,
          critice, medii, scazute,
          dosar_id: activeDosarId || null,
        }),
      });
      showSaveToast(res.ok);
    } catch { showSaveToast(false); }
  };

  const saveTraining = async () => {
    if (!trainingResult) return;
    const meta = trainingMeta || {};
    try {
      const res = await authFetch('/api/v1/saved/training', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tip_material: trainingTip,
          tema: trainingTema,
          nivel_dificultate: trainingNivel,
          lungime: trainingLungime,
          full_content: trainingEditedResult || trainingResult,
          material: meta.material || null,
          cerinte: meta.cerinte || null,
          rezolvare: meta.rezolvare || null,
          note_trainer: meta.note_trainer || null,
          legislatie_citata: meta.legislatie_citata || [],
          jurisprudenta_citata: meta.jurisprudenta_citata || [],
          metadata: { tip_name: meta.tip_name || trainingTip },
          dosar_id: activeDosarId || null,
        }),
      });
      showSaveToast(res.ok);
    } catch { showSaveToast(false); }
  };

  const loadHistory = async (type: string) => {
    if (historyPanel === type) { setHistoryPanel(null); return; }
    setHistoryPanel(type);
    setHistoryLoading(true);
    try {
      const urlMap: Record<string, string> = {
        conversations: '/api/v1/saved/conversations',
        contestatie: '/api/v1/saved/documents?tip=contestatie',
        clarificare: '/api/v1/saved/documents?tip=clarificare',
        rag_memo: '/api/v1/saved/documents?tip=rag_memo',
        redflags: '/api/v1/saved/redflags',
        training: '/api/v1/saved/training',
      };
      const res = await authFetch(urlMap[type] || '/api/v1/saved/conversations');
      const data = await res.json();
      setHistoryItems(data);
    } catch { setHistoryItems([]); }
    setHistoryLoading(false);
  };

  const loadConversation = async (id: string) => {
    try {
      const res = await authFetch(`/api/v1/saved/conversations/${id}`);
      const data = await res.json();
      setChatMessages(data.mesaje.map((m: any) => ({ role: m.rol, text: m.continut, citations: m.citations })));
      setHistoryPanel(null);
    } catch { showSaveToast(false, 'Eroare la încărcare'); }
  };

  const loadDocument = async (id: string, targetMode: AppMode) => {
    try {
      const res = await authFetch(`/api/v1/saved/documents/${id}`);
      const data = await res.json();
      setGeneratedContent(data.continut);
      setGeneratedDecisionRefs(data.referinte_decizii || []);
      setMode(targetMode);
      setHistoryPanel(null);
    } catch { showSaveToast(false, 'Eroare la încărcare'); }
  };

  const loadRedFlags = async (id: string) => {
    try {
      const res = await authFetch(`/api/v1/saved/redflags/${id}`);
      const data = await res.json();
      setRedFlagsResults(data.rezultate);
      setSelectedRedFlags([]);
      const edits: Record<number, string> = {};
      data.rezultate.forEach((rf: any, i: number) => { if (rf.edited_clarification) edits[i] = rf.edited_clarification; });
      setEditedClarifications(edits);
      setHistoryPanel(null);
    } catch { showSaveToast(false, 'Eroare la încărcare'); }
  };

  const loadTraining = async (id: string) => {
    try {
      const res = await authFetch(`/api/v1/saved/training/${id}`);
      const data = await res.json();
      setTrainingResult(data.full_content);
      setTrainingTema(data.tema);
      setTrainingTip(data.tip_material);
      setTrainingNivel(data.nivel_dificultate);
      setTrainingLungime(data.lungime);
      setTrainingMeta({ material: data.material, cerinte: data.cerinte, rezolvare: data.rezolvare, note_trainer: data.note_trainer, legislatie_citata: data.legislatie_citata, jurisprudenta_citata: data.jurisprudenta_citata });
      setTrainingEditedResult(null);
      setTrainingEditing(false);
      setHistoryPanel(null);
    } catch { showSaveToast(false, 'Eroare la încărcare'); }
  };

  const deleteHistoryItem = async (type: string, id: string) => {
    const urlMap: Record<string, string> = {
      conversations: `/api/v1/saved/conversations/${id}`,
      contestatie: `/api/v1/saved/documents/${id}`,
      clarificare: `/api/v1/saved/documents/${id}`,
      rag_memo: `/api/v1/saved/documents/${id}`,
      redflags: `/api/v1/saved/redflags/${id}`,
      training: `/api/v1/saved/training/${id}`,
    };
    try {
      await authFetch(urlMap[type], { method: 'DELETE' });
      setHistoryItems(prev => prev.filter(item => item.id !== id));
    } catch { showSaveToast(false, 'Eroare la ștergere'); }
  };

  // === END SAVE & HISTORY HELPERS ===

  const handleChat = async () => {
    if (!chatInput.trim()) return;
    const userMsg = chatInput;
    setChatMessages(prev => [...prev, { role: 'user', text: userMsg }]);
    setChatInput("");
    setIsLoading(true);
    setStreamStatus("Se caută în baza de date...");

    // Add placeholder for streaming response
    setChatMessages(prev => [...prev, { role: 'model', text: '' }]);

    try {
      let accumulated = '';
      await authFetchStream(
        '/api/v1/chat/stream',
        {
          message: userMsg,
          history: chatMessages.map(m => ({
            role: m.role === 'model' ? 'assistant' : m.role,
            content: m.text
          })),
          scope_id: activeScopeId || undefined,
          rerank: enableReranking || undefined,
          expansion: enableExpansion || undefined,
        },
        (chunk) => {
          accumulated += chunk;
          setChatMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: 'model', text: accumulated };
            return updated;
          });
        },
        (meta) => {
          // Log timing for Network/Console tab visibility
          if (meta.search_duration_s !== undefined) {
            console.log(`[ExpertAP] Search duration: ${meta.search_duration_s}s`);
          }

          // Append citations + timing on completion
          let suffix = "";
          if (meta.citations && meta.citations.length > 0) {
            suffix += "\n\n📚 **Surse:** " + meta.citations.slice(0, 10).map((c: any) => `[[${c.decision_id}]]`).join(" ");
          }
          if (meta.search_duration_s !== undefined) {
            suffix += `\n\n⏱ *Căutare: ${meta.search_duration_s}s*`;
          }
          if (suffix) {
            accumulated += suffix;
            setChatMessages(prev => {
              const updated = [...prev];
              updated[updated.length - 1] = { role: 'model', text: accumulated };
              return updated;
            });
          }
        },
        (error) => {
          setChatMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: 'model', text: `Eroare: ${error}` };
            return updated;
          });
        },
        (status) => setStreamStatus(status),
      );
    } catch (err) {
      console.error(err);
      setChatMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: 'model',
          text: "Eroare la procesarea cererii. Asigură-te că backend-ul este pornit și conectat la baza de date."
        };
        return updated;
      });
    } finally {
      setIsLoading(false);
      setStreamStatus("");
      refreshAuthUser();
    }
  };

  const handleDrafting = async () => {
    setIsLoading(true);
    setStreamStatus("Se caută jurisprudență relevantă...");
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);

    try {
      await authFetchStream(
        '/api/v1/drafter/stream',
        {
          facts: drafterContext.facts,
          authority_args: drafterContext.authorityArgs,
          legal_grounds: drafterContext.legalGrounds,
          scope_id: activeScopeId || undefined,
          doc_type: drafterDocType,
          remedii_solicitate: drafterContext.remediiSolicitate || undefined,
          detalii_procedura: drafterContext.detaliiProcedura || undefined,
          numar_decizie_cnsc: drafterContext.numarDecizieCnsc || undefined,
        },
        (chunk) => {
          setGeneratedContent(prev => prev + chunk);
        },
        (meta) => {
          if (meta.decision_refs) {
            setGeneratedDecisionRefs(meta.decision_refs);
          }
        },
        (error) => {
          setGeneratedContent(`Eroare: ${error}`);
        },
        (status) => setStreamStatus(status),
      );
    } catch (err) {
      console.error(err);
      setGeneratedContent("Eroare la generare. Verifică că backend-ul este pornit.");
    } finally {
      setIsLoading(false);
      setStreamStatus("");
      refreshAuthUser();
    }
  };

  const handleDocumentUpload = async (
    event: React.ChangeEvent<HTMLInputElement>,
    onTextExtracted?: (text: string) => void,
    appendDoc?: (doc: {name: string, text: string}) => void,
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Check file type
    const allowedTypes = ['.txt', '.md', '.pdf', '.doc', '.docx'];
    const extension = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!allowedTypes.includes(extension)) {
      alert('Tip de fișier nesuportat. Folosește .txt, .md, .pdf, .doc sau .docx');
      return;
    }

    setIsLoading(true);
    try {
      // Convert to base64
      const base64 = await fileToBase64(file);

      // Call backend to extract text
      const response = await authFetch('/api/v1/documents/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          filename: file.name,
          content: base64,
          mime_type: file.type
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      if (appendDoc) {
        appendDoc({ name: file.name, text: data.text });
      }
      if (onTextExtracted) {
        onTextExtracted(data.text);
      }

    } catch (err) {
      console.error(err);
      alert('Eroare la procesarea documentului. Verifică că backend-ul este pornit.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleRedFlags = async () => {
    const textToAnalyze = redFlagsTab === 'upload' && uploadedDocsRedFlags.length > 0
      ? uploadedDocsRedFlags.map((doc, i) => `=== DOCUMENT ${i + 1}: ${doc.name} ===\n${doc.text}`).join('\n\n---\n\n')
      : redFlagsText;

    if (!textToAnalyze || textToAnalyze.trim().length < 10) {
      alert("Introduceți text pentru analiză (min. 10 caractere) sau încărcați un document.");
      return;
    }

    setIsLoading(true);
    setRedFlagsResults([]);
    setSelectedRedFlags([]);
    setEditedClarifications({});
    setRedFlagsProgress("Se trimite documentul pentru analiză...");

    // Progress simulation — shows user what's happening during long analysis
    const charCount = textToAnalyze.length;
    const isLargeDoc = charCount > 15000;
    const progressTimer = setTimeout(() => {
      setRedFlagsProgress(
        isLargeDoc
          ? "Document mare detectat — se analizează în secțiuni paralele..."
          : "Se identifică clauzele problematice..."
      );
    }, 3000);
    const progressTimer2 = setTimeout(() => {
      setRedFlagsProgress("Se verifică cu legislația și jurisprudența CNSC...");
    }, isLargeDoc ? 20000 : 15000);
    const progressTimer3 = setTimeout(() => {
      setRedFlagsProgress("Se fundamentează fiecare red flag cu articole reale...");
    }, isLargeDoc ? 45000 : 30000);

    // AbortController with 180s timeout for large documents
    const controller = new AbortController();
    const timeoutMs = isLargeDoc ? 180000 : 120000;
    const fetchTimeout = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await authFetch('/api/v1/redflags/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          text: textToAnalyze,
          use_jurisprudence: true
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => null);
        const detail = errorData?.detail || `Eroare server (${response.status})`;
        throw new Error(detail);
      }

      const data = await response.json();
      setRedFlagsResults(data.red_flags || []);

    } catch (err: any) {
      console.error(err);
      if (err.name === 'AbortError') {
        alert(
          `Analiza a depășit timpul limită (${timeoutMs / 1000}s). ` +
          'Documentul poate fi prea complex. Încearcă cu o secțiune mai mică.'
        );
      } else {
        alert(`Eroare la analiză: ${err.message || 'Verifică că backend-ul este pornit.'}`);
      }
    } finally {
      clearTimeout(progressTimer);
      clearTimeout(progressTimer2);
      clearTimeout(progressTimer3);
      clearTimeout(fetchTimeout);
      setIsLoading(false);
      setRedFlagsProgress("");
    }
  };

  const handleRAGMemoExport = async (format: 'docx' | 'pdf' | 'md') => {
    if (!generatedContent) return;
    try {
      const response = await authFetch('/api/v1/training/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: generatedContent,
          format,
          titlu: `Memo RAG — ${memoTopic.slice(0, 100) || 'Jurisprudență'}`,
          metadata: {},
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ext = format === 'docx' ? 'docx' : format === 'pdf' ? 'pdf' : 'md';
      a.download = `Memo_RAG_Jurisprudenta.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
      alert('Eroare la export. Verificați consola.');
    }
  };

  const handleRedFlagsExport = async (format: 'docx' | 'pdf' | 'md') => {
    if (selectedRedFlags.length === 0) return;

    // Assemble clarification document from selected flags
    const docNames = uploadedDocsRedFlags.map(d => d.name);
    const header = `Către: Autoritatea Contractantă\nRef: Solicitare de Clarificări / Modificare Documentație de Atribuire\n${docNames.length ? `Documente analizate: ${docNames.join(', ')}\n` : ''}\n---\n\n`;

    const sections = selectedRedFlags.map((flagIdx, i) => {
      const flag = redFlagsResults[flagIdx];
      const proposal = editedClarifications[flagIdx] ?? flag.clarification_proposal ?? '';
      return `### ${i + 1}. Red Flag — Severitate: ${flag.severity}\n\n${proposal || `Având în vedere cerința din documentația de atribuire conform căreia «${flag.clause}», faptul că ${flag.issue}${flag.legal_references?.length ? `, ${flag.legal_references.map((r: any) => `${r.citare} din ${r.act_normativ}`).join(', ')}` : ''}${flag.decision_refs?.length ? `, Deciziile CNSC: ${flag.decision_refs.join(', ')}` : ''}, vă solicităm să fiți de acord cu reformularea cerinței după cum urmează: ${flag.recommendation || '[a se completa]'}`}\n`;
    });

    const footer = `\n---\n\nCu stimă,\n[Operator Economic]`;
    const fullContent = header + sections.join('\n') + footer;

    try {
      const response = await authFetch('/api/v1/training/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: fullContent,
          format,
          titlu: 'Solicitare de Clarificări — Red Flags',
          metadata: {},
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ext = format === 'docx' ? 'docx' : format === 'pdf' ? 'pdf' : 'md';
      a.download = `Solicitare_Clarificari_RedFlags.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
      alert('Eroare la export. Verificați consola.');
    }
  };

  const handleClarification = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);
    try {
      const response = await authFetch('/api/v1/clarification/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clause: clarificationClause })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      setGeneratedContent(data.content || "");
      setGeneratedDecisionRefs(data.decision_refs || []);
    } catch (err) {
      console.error(err);
      setGeneratedContent("Eroare la generare. Verifică că backend-ul este pornit.");
    } finally {
      setIsLoading(false);
    }
  };

  // --- ANAP Spete Page Functions ---
  const fetchSpete = async (page = 1, search = '', categorie = '', tag = '', semantic = false) => {
    setIsLoadingSpete(true);
    try {
      const params = new URLSearchParams({ page: String(page), limit: '20' });
      if (search.trim()) params.set('search', search.trim());
      if (categorie) params.set('categorie', categorie);
      if (tag) params.set('tag', tag);
      if (semantic && search.trim()) params.set('semantic', 'true');
      const res = await authFetch(`/api/v1/spete/?${params}`);
      if (res.ok) {
        const data = await res.json();
        setSpete(data.items);
        setSpeteTotal(data.total);
        setSpetePages(data.pages);
        setSpetePage(data.page);
      }
    } catch (err) { console.error('Failed to fetch spete:', err); }
    finally { setIsLoadingSpete(false); }
  };

  const fetchSpeteFilters = async () => {
    try {
      const [catRes, tagRes, statsRes] = await Promise.all([
        authFetch('/api/v1/spete/categories'),
        authFetch('/api/v1/spete/tags'),
        authFetch('/api/v1/spete/stats'),
      ]);
      if (catRes.ok) setSpeteCategories(await catRes.json());
      if (tagRes.ok) setSpeteTags(await tagRes.json());
      if (statsRes.ok) setSpeteStats(await statsRes.json());
    } catch (err) { console.error('Failed to fetch spete filters:', err); }
  };

  useEffect(() => {
    if (mode === 'spete') {
      fetchSpete(1, speteSearch, speteFilterCat, speteFilterTag, speteSemantic);
      fetchSpeteFilters();
    }
  }, [mode]);

  // Debounced search for spete
  useEffect(() => {
    if (mode !== 'spete') return;
    const t = setTimeout(() => {
      fetchSpete(1, speteSearch, speteFilterCat, speteFilterTag, speteSemantic);
    }, 300);
    return () => clearTimeout(t);
  }, [speteSearch, speteFilterCat, speteFilterTag, speteSemantic]);

  const handleRAGMemo = async () => {
    if (!memoTopic || memoTopic.trim().length < 3) {
      alert("Introduceți un topic pentru memo juridic (min. 3 caractere).");
      return;
    }

    setIsLoading(true);
    setStreamStatus("Se caută jurisprudență relevantă...");
    setGeneratedContent("");
    setRagMemoSaved(false);

    try {
      await authFetchStream(
        '/api/v1/ragmemo/stream',
        {
          topic: memoTopic,
          max_decisions: 5,
          scope_id: activeScopeId || undefined,
        },
        (chunk) => {
          setGeneratedContent(prev => prev + chunk);
        },
        (_meta) => {
          // Memo content is already streamed, metadata not used in UI
        },
        (error) => {
          setGeneratedContent(`Eroare: ${error}`);
        },
        (status) => setStreamStatus(status),
      );
    } catch (err) {
      console.error(err);
      setGeneratedContent("Eroare la generarea memo-ului. Verifică că backend-ul este pornit și conectat la baza de date.");
    } finally {
      setIsLoading(false);
      setStreamStatus("");
      refreshAuthUser();
    }
  };


  // --- Render Functions ---

  const MODE_LABELS: Record<AppMode, string> = {
    dashboard: 'Dashboard',
    datalake: 'Decizii CNSC',
    spete: 'Spețe ANAP',
    chat: 'Asistent AP',
    drafter: 'Drafter Contestații',
    redflags: 'Red Flags Detector',
    clarification: 'Clarificări',
    rag: 'Jurisprudență RAG',
    training: 'TrainingAP',
    dosare: 'Dosare Digitale',
    alerts: 'Alerte Decizii',
    settings: 'Setări LLM',
    profile: 'Profil',
    pricing: 'Planuri & Prețuri',
  };

  const renderSidebar = () => (
    <>
      {/* Mobile header bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 bg-slate-900 border-b border-slate-800 flex items-center gap-3 px-4 py-3">
        <button onClick={() => setSidebarOpen(true)} className="text-white p-1">
          <Menu size={22} />
        </button>
        <div className="flex items-center gap-2">
          <AppLogo size={24} className="rounded" />
          <span className="text-white font-bold text-sm">ExpertAP</span>
        </div>
        <div className="flex items-center gap-1.5 ml-auto">
          <div className={`w-2 h-2 rounded-full ${dbStats !== null && (dbStats?.total_decisions || 0) > 0 ? 'bg-green-400' : 'bg-slate-500'}`}></div>
          <span className="text-slate-400 text-xs">{MODE_LABELS[mode]}</span>
        </div>
      </div>

      {/* Backdrop for mobile */}
      {sidebarOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/50 z-40"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div className={`
        w-72 bg-slate-900 h-screen flex flex-col border-r border-slate-800 shrink-0 text-slate-300
        fixed md:static z-50 transition-transform duration-300 ease-in-out
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
      `}>
      <div className="p-6 border-b border-slate-800 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2.5 tracking-tight">
          <AppLogo size={36} className="rounded-lg" />
          ExpertAP
        </h1>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${dbStats !== null && (dbStats?.total_decisions || 0) > 0 ? 'bg-green-400 animate-pulse' : dbStats === null ? 'bg-yellow-400 animate-pulse' : 'bg-slate-500'}`} title={dbStats !== null && (dbStats?.total_decisions || 0) > 0 ? 'Conectat' : 'Deconectat'}></div>
          <button onClick={() => setSidebarOpen(false)} className="md:hidden text-slate-400 hover:text-white p-1">
            <X size={20} />
          </button>
        </div>
      </div>
      <p className="text-xs text-slate-500 px-6 pt-2 font-medium">Platformă de Business Intelligence <br/>pentru Achiziții Publice</p>

      <nav className="flex-1 overflow-y-auto px-4 py-6 space-y-8">
        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Workspace</div>
           <SidebarItem icon={MessageSquare} label="Asistent AP" active={mode === 'chat'} onClick={() => { setMode('chat'); setSidebarOpen(false); }} />
           {canAccess('datalake') ? (
             <SidebarItem icon={Filter} label="Decizii CNSC" active={mode === 'datalake'} onClick={() => { setMode('datalake'); setSidebarOpen(false); }} badge={files.length} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Creează un cont gratuit">
               <SidebarItem icon={Filter} label="Decizii CNSC" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('spete') ? (
             <SidebarItem icon={Layers} label="Spețe ANAP" active={mode === 'spete'} onClick={() => { setMode('spete'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Creează un cont gratuit">
               <SidebarItem icon={Layers} label="Spețe ANAP" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('dashboard') ? (
             <SidebarItem icon={LayoutDashboard} label="Dashboard" active={mode === 'dashboard'} onClick={() => { setMode('dashboard'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Creează un cont gratuit">
               <SidebarItem icon={LayoutDashboard} label="Dashboard" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('analytics') ? (
             <SidebarItem icon={TrendingUp} label="Analiză CNSC" active={mode === 'analytics'} onClick={() => { setMode('analytics'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Creează un cont gratuit">
               <SidebarItem icon={TrendingUp} label="Analiză CNSC" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('strategy') ? (
             <SidebarItem icon={Target} label="Strategie Contestare" active={mode === 'strategy'} onClick={() => { setMode('strategy'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Basic">
               <SidebarItem icon={Target} label="Strategie Contestare" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('dosare') ? (
             <SidebarItem icon={Briefcase} label="Dosare Digitale" active={mode === 'dosare'} onClick={() => { setMode('dosare'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Basic">
               <SidebarItem icon={Briefcase} label="Dosare Digitale" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('alerts') ? (
             <SidebarItem icon={Bell} label="Alerte Decizii" active={mode === 'alerts'} onClick={() => { setMode('alerts'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Basic">
               <SidebarItem icon={Bell} label="Alerte Decizii" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Instrumente Juridice</div>
           {canAccess('multi_document') ? (
             <SidebarItem icon={Files} label="Analiză Multi-Document" active={mode === 'multi_document' as any} onClick={() => { setMode('multi_document' as any); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Pro">
               <SidebarItem icon={Files} label="Analiză Multi-Document" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('compliance') ? (
             <SidebarItem icon={ClipboardCheck} label="Verificator Conformitate" active={mode === 'compliance'} onClick={() => { setMode('compliance'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Basic">
               <SidebarItem icon={ClipboardCheck} label="Verificator Conformitate" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('drafter') ? (
             <SidebarItem icon={Scale} label="Drafter Contestații" active={mode === 'drafter'} onClick={() => { setMode('drafter'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Basic">
               <SidebarItem icon={Scale} label="Drafter Contestații" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('redflags') ? (
             <SidebarItem icon={AlertTriangle} label="Red Flags Detector" active={mode === 'redflags'} onClick={() => { setMode('redflags'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Basic">
               <SidebarItem icon={AlertTriangle} label="Red Flags Detector" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('clarification') ? (
             <SidebarItem icon={Search} label="Clarificări" active={mode === 'clarification'} onClick={() => { setMode('clarification'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Basic">
               <SidebarItem icon={Search} label="Clarificări" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
           {canAccess('rag') ? (
             <SidebarItem icon={BookOpen} label="Jurisprudență RAG" active={mode === 'rag'} onClick={() => { setMode('rag'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Creează un cont gratuit">
               <SidebarItem icon={BookOpen} label="Jurisprudență RAG" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Formare</div>
           {canAccess('training') ? (
             <SidebarItem icon={GraduationCap} label="TrainingAP" active={mode === 'training'} onClick={() => { setMode('training'); setSidebarOpen(false); }} />
           ) : (
             <div className="opacity-50 cursor-not-allowed" title="Disponibil în planul Pro">
               <SidebarItem icon={GraduationCap} label="TrainingAP" active={false} onClick={() => setShowAuthModal(true)} />
             </div>
           )}
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Sistem</div>
           {canAccess('settings') && (
             <SidebarItem icon={Settings} label="Setări LLM" active={mode === 'settings'} onClick={() => { setMode('settings'); setSidebarOpen(false); }} />
           )}
           <SidebarItem icon={Package} label="Planuri & Prețuri" active={mode === 'pricing'} onClick={() => { setMode('pricing'); setSidebarOpen(false); }} />
        </div>
      </nav>

      {/* Sidebar bottom: user info or login prompt */}
      <div className="p-4 border-t border-slate-800 bg-slate-900/50">
        {authState.user ? (
          <div>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold text-xs shrink-0 cursor-pointer hover:ring-2 hover:ring-blue-400 transition-all" onClick={() => { setMode('profile'); setSidebarOpen(false); }} title="Profil">
                {(authState.user.nume || authState.user.email)[0].toUpperCase()}
              </div>
              <div className="flex-1 min-w-0 cursor-pointer" onClick={() => { setMode('profile'); setSidebarOpen(false); }} title="Profil">
                <p className="text-sm text-white font-medium truncate hover:text-blue-300 transition-colors">{authState.user.nume || authState.user.email}</p>
                <p className="text-xs text-slate-400">{PLAN_LABELS[authState.user.rol] || authState.user.rol}</p>
              </div>
              <button onClick={handleLogout} className="text-slate-400 hover:text-white p-1" title="Deconectare">
                <LogOut size={16} />
              </button>
            </div>
            {!authState.user.email_verified && (
              <div className="mt-2 p-2 rounded-lg bg-yellow-500/10 border border-yellow-500/30 cursor-pointer hover:bg-yellow-500/20 transition-colors" onClick={() => { setMode('profile'); setSidebarOpen(false); }}>
                <p className="text-xs text-yellow-400 font-medium">Email neverificat</p>
                <p className="text-[10px] text-yellow-500/70">Click pentru verificare</p>
              </div>
            )}
            <div className="mt-2">
              <div className="flex justify-between text-xs text-slate-500 mb-1">
                <span>{authState.user.queries_today}/{authState.user.queries_limit} interogări azi</span>
              </div>
              <div className="w-full bg-slate-700 rounded-full h-1">
                <div className="bg-blue-500 rounded-full h-1 transition-all" style={{width: `${Math.min(100, (authState.user.queries_today / authState.user.queries_limit) * 100)}%`}} />
              </div>
            </div>
            {/* LLM provider mini-info */}
            <div className={`mt-2 flex items-center gap-2 rounded p-1 -mx-1 ${canAccess('settings') ? 'cursor-pointer hover:bg-slate-800/50' : ''}`} onClick={() => { if (canAccess('settings')) { setMode('settings'); setSidebarOpen(false); } }}>
              <div className={`w-5 h-5 rounded-full flex items-center justify-center text-white font-bold text-[8px] ${
                llmSettings?.active_provider === 'anthropic' ? 'bg-orange-500' : llmSettings?.active_provider === 'groq' ? 'bg-purple-500' : llmSettings?.active_provider === 'openai' ? 'bg-green-500' : 'bg-blue-500'
              }`}>AI</div>
              <span className="text-xs text-slate-500 truncate">
                {llmSettings?.active_model?.replace(/-preview$/, '').replace(/^gemini-/, '').replace(/^claude-/, '').replace(/-versatile$/, '').replace(/:free$/, ' ★') || llmSettings?.active_provider || 'Gemini'}
              </span>
              <div className={`w-1.5 h-1.5 rounded-full ml-auto ${llmSettings?.providers?.[llmSettings?.active_provider || '']?.configured ? 'bg-green-400' : 'bg-yellow-400'}`} />
            </div>
          </div>
        ) : (
          <div>
            <button onClick={() => { setShowAuthModal(true); setAuthMode('login'); }} className="w-full flex items-center gap-3 p-2 rounded-lg hover:bg-slate-800 transition-colors">
              <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center">
                <Shield size={16} className="text-slate-400" />
              </div>
              <div className="text-left">
                <p className="text-sm text-white font-medium">Conectează-te</p>
                <p className="text-xs text-slate-500">Salvează și accesează instrumentele</p>
              </div>
            </button>
            {/* LLM provider mini-info */}
            <div className="mt-2 flex items-center gap-2 cursor-pointer hover:bg-slate-800/50 rounded p-1 -mx-1" onClick={() => { setMode('settings'); setSidebarOpen(false); }}>
              <div className={`w-5 h-5 rounded-full flex items-center justify-center text-white font-bold text-[8px] ${
                llmSettings?.active_provider === 'anthropic' ? 'bg-orange-500' : llmSettings?.active_provider === 'groq' ? 'bg-purple-500' : llmSettings?.active_provider === 'openai' ? 'bg-green-500' : 'bg-blue-500'
              }`}>AI</div>
              <span className="text-xs text-slate-500 truncate">
                {llmSettings?.active_model?.replace(/-preview$/, '').replace(/^gemini-/, '').replace(/^claude-/, '').replace(/-versatile$/, '').replace(/:free$/, ' ★') || llmSettings?.active_provider || 'Gemini'}
              </span>
              <div className={`w-1.5 h-1.5 rounded-full ml-auto ${llmSettings?.providers?.[llmSettings?.active_provider || '']?.configured ? 'bg-green-400' : 'bg-yellow-400'}`} />
            </div>
          </div>
        )}
      </div>
    </div>
    </>
  );

  const renderDashboard = () => {
    const totalDecisions = dbStats?.total_decisions || 0;
    const admisCount = dbStats?.by_ruling?.['ADMIS'] || 0;
    const admisPartialCount = dbStats?.by_ruling?.['ADMIS_PARTIAL'] || 0;
    const admisTotal = admisCount + admisPartialCount;
    const respinsCount = dbStats?.by_ruling?.['RESPINS'] || 0;
    const rezultatCount = dbStats?.by_type?.['rezultat'] || 0;
    const documentatieCount = dbStats?.by_type?.['documentatie'] || 0;
    const necunoscutCount = totalDecisions - rezultatCount - documentatieCount;
    const isConnected = dbStats !== null && totalDecisions > 0;
    const winRatePct = totalDecisions > 0 ? Math.round((admisTotal) / totalDecisions * 100) : 0;
    const respinsPct = totalDecisions > 0 ? Math.round(respinsCount / totalDecisions * 100) : 0;

    // Donut chart SVG helper
    const DonutSegment = ({ pct, offset, color }: { pct: number, offset: number, color: string }) => {
      const r = 40;
      const c = 2 * Math.PI * r;
      const dashLen = (pct / 100) * c;
      const dashOff = -(offset / 100) * c;
      return <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="12" strokeDasharray={`${dashLen} ${c - dashLen}`} strokeDashoffset={dashOff} strokeLinecap="round" className="transition-all duration-700" />;
    };

    return (
    <div className="h-full overflow-y-auto">
    <div className="p-4 md:p-8 max-w-7xl mx-auto animate-in fade-in duration-500">
      {/* Header */}
      <header className="mb-6 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div className="flex items-center gap-3">
          <AppLogo size={40} className="rounded-xl hidden sm:block" />
          <div>
            <h2 className="text-2xl md:text-3xl font-bold text-slate-900">Dashboard</h2>
            <p className="text-sm text-slate-500">Centrul de comandă ExpertAP</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-white border border-slate-200 px-3 py-1.5 rounded-full shadow-sm">
            <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : dbStats === null ? 'bg-yellow-500 animate-pulse' : 'bg-slate-300'}`}></div>
            <span className="text-xs font-medium text-slate-600">
              {isConnected ? 'PostgreSQL' : dbStats === null ? 'Conectare...' : 'Deconectat'}
            </span>
          </div>
        </div>
      </header>

      {/* Row 1: Central Donut + Type Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        {/* Central Donut Chart - Rata de Succes */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm flex flex-col items-center">
          <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2 self-start">
            <TrendingUp size={18} className="text-blue-500" />
            Rata de Succes Contestații
          </h3>
          <div className="relative w-40 h-40 mb-3">
            <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
              <circle cx="50" cy="50" r="40" fill="none" stroke="#f1f5f9" strokeWidth="12" />
              <DonutSegment pct={totalDecisions > 0 ? (admisCount / totalDecisions) * 100 : 0} offset={0} color="#10b981" />
              <DonutSegment pct={totalDecisions > 0 ? (admisPartialCount / totalDecisions) * 100 : 0} offset={totalDecisions > 0 ? (admisCount / totalDecisions) * 100 : 0} color="#f59e0b" />
              <DonutSegment pct={respinsPct} offset={totalDecisions > 0 ? (admisTotal / totalDecisions) * 100 : 0} color="#ef4444" />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-3xl font-bold text-slate-800">{winRatePct}%</span>
              <span className="text-[10px] text-slate-500 font-medium">Admise</span>
            </div>
          </div>
          <div className="flex gap-4 text-xs">
            <div className="flex items-center gap-1.5"><div className="w-2.5 h-2.5 rounded-full bg-emerald-500"></div><span className="text-slate-600">Admis ({admisCount})</span></div>
            <div className="flex items-center gap-1.5"><div className="w-2.5 h-2.5 rounded-full bg-amber-500"></div><span className="text-slate-600">Parțial ({admisPartialCount})</span></div>
            <div className="flex items-center gap-1.5"><div className="w-2.5 h-2.5 rounded-full bg-red-500"></div><span className="text-slate-600">Respins ({respinsCount})</span></div>
          </div>
        </div>

        {/* Type Breakdown: Documentație vs Rezultat */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm lg:col-span-2">
          <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2">
            <Layers size={18} className="text-purple-500" />
            Defalcare pe Tip Contestație
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
            <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-center">
              <div className="text-3xl font-bold text-blue-700">{totalDecisions.toLocaleString()}</div>
              <div className="text-xs text-blue-600 mt-1 font-medium">Total Decizii</div>
            </div>
            <button onClick={() => { setFilterType('documentatie'); setMode('datalake'); }} className="bg-purple-50 border border-purple-100 rounded-xl p-4 text-center hover:bg-purple-100 transition cursor-pointer">
              <div className="text-3xl font-bold text-purple-700">{documentatieCount.toLocaleString()}</div>
              <div className="text-xs text-purple-600 mt-1 font-medium">Documentație</div>
            </button>
            <button onClick={() => { setFilterType('rezultat'); setMode('datalake'); }} className="bg-orange-50 border border-orange-100 rounded-xl p-4 text-center hover:bg-orange-100 transition cursor-pointer">
              <div className="text-3xl font-bold text-orange-700">{rezultatCount.toLocaleString()}</div>
              <div className="text-xs text-orange-600 mt-1 font-medium">Rezultat</div>
            </button>
          </div>
          {/* Win rate per type */}
          {winRateByCategory.length > 0 ? (
            <div className="space-y-3">
              {winRateByCategory.map((cat: any) => {
                const catColors: Record<string, {bar: string, bg: string, text: string}> = {
                  'Servicii': { bar: 'bg-blue-500', bg: 'bg-blue-50', text: 'text-blue-700' },
                  'Furnizare': { bar: 'bg-orange-500', bg: 'bg-orange-50', text: 'text-orange-700' },
                  'Lucrări': { bar: 'bg-green-500', bg: 'bg-green-50', text: 'text-green-700' },
                };
                const colors = catColors[cat.category] || { bar: 'bg-slate-500', bg: 'bg-slate-50', text: 'text-slate-700' };
                const docType = cat.by_type?.find((t: any) => t.type === 'documentatie');
                const rezType = cat.by_type?.find((t: any) => t.type === 'rezultat');
                return (
                  <div key={cat.category} className={`${colors.bg} rounded-lg p-3 border border-slate-100`}>
                    <div className="flex justify-between items-center mb-2">
                      <button onClick={() => { setFilterCategorie(cat.category); setMode('datalake'); }} className={`font-semibold text-sm ${colors.text} hover:underline`}>
                        {cat.category}
                      </button>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-500">{cat.total} decizii</span>
                        <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${cat.win_rate >= 50 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
                          {cat.win_rate}% admise
                        </span>
                      </div>
                    </div>
                    <div className="w-full bg-white/60 rounded-full h-2 mb-1.5">
                      <div className={`${colors.bar} h-2 rounded-full transition-all duration-500`} style={{ width: `${(cat.total / totalDecisions) * 100}%` }}></div>
                    </div>
                    <div className="flex gap-4 text-[10px] text-slate-500">
                      {docType && <span>Doc: {docType.total} ({docType.win_rate}% admise)</span>}
                      {rezType && <span>Rez: {rezType.total} ({rezType.win_rate}% admise)</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : categoriiStats.length > 0 ? (
            <div className="space-y-3">
              {categoriiStats.map((cat, idx) => {
                const totalCat = categoriiStats.reduce((s, c) => s + c.count, 0);
                const pct = totalCat > 0 ? Math.round((cat.count / totalCat) * 100) : 0;
                const colors = ['bg-blue-500', 'bg-orange-500', 'bg-green-500'];
                const bgColors = ['bg-blue-50', 'bg-orange-50', 'bg-green-50'];
                return (
                  <div key={cat.name} className={`${bgColors[idx % 3]} rounded-lg p-3`}>
                    <div className="flex justify-between items-center mb-1">
                      <button onClick={() => { setFilterCategorie(cat.name); setMode('datalake'); }} className="text-sm font-semibold text-slate-700 hover:underline">{cat.name}</button>
                      <span className="text-xs text-slate-500">{cat.count} ({pct}%)</span>
                    </div>
                    <div className="w-full bg-white/60 rounded-full h-2">
                      <div className={`${colors[idx % 3]} h-2 rounded-full transition-all duration-500`} style={{ width: `${pct}%` }}></div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>

      {/* Row 2: Top CPV Groups (3-digit prefix, horizontal bar chart with win rate) */}
      {cpvTopGrouped.length > 0 && (
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm mb-6">
        <h3 className="font-bold text-slate-800 mb-1 flex items-center gap-2">
          <BarChart3 size={18} className="text-purple-500" />
          Top Grupuri CPV — Volume și Rata de Admisibilitate
        </h3>
        <p className="text-[10px] text-slate-400 mb-4">Coduri CPV grupate pe primele 3 cifre (ex: 331 = Medical, 555 = Catering)</p>
        <div className="space-y-2">
          {cpvTopGrouped.map((cpv: any) => {
            const maxCount = cpvTopGrouped[0]?.total || 1;
            const barPct = Math.round((cpv.total / maxCount) * 100);
            const catLabel = cpv.categorie === 'Servicii' ? 'S' : cpv.categorie === 'Furnizare' ? 'F' : cpv.categorie === 'Lucrări' ? 'L' : '?';
            const catColor = cpv.categorie === 'Servicii' ? 'bg-blue-100 text-blue-700' : cpv.categorie === 'Furnizare' ? 'bg-orange-100 text-orange-700' : cpv.categorie === 'Lucrări' ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-700';
            const winColor = cpv.win_rate >= 60 ? 'text-emerald-600' : cpv.win_rate >= 40 ? 'text-amber-600' : 'text-red-600';
            return (
              <div
                key={cpv.code}
                className="w-full text-left group hover:bg-slate-50 rounded-lg px-2 py-2 transition"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${catColor}`}>{catLabel}</span>
                  <span className="text-xs font-mono font-bold text-slate-600 w-12 shrink-0">{cpv.code}*</span>
                  <span className="text-xs text-slate-500 truncate flex-1" title={cpv.description}>{cpv.description || ''}</span>
                  {cpv.cpv_count > 1 && <span className="text-[9px] text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded shrink-0">{cpv.cpv_count} coduri</span>}
                  <span className={`text-xs font-bold ${winColor} tabular-nums shrink-0`}>{cpv.win_rate}%</span>
                  <span className="text-xs text-slate-500 tabular-nums shrink-0 w-10 text-right">{cpv.total}</span>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-2 relative">
                  <div className="bg-slate-300 h-2 rounded-full transition-all duration-500" style={{ width: `${barPct}%` }}></div>
                  <div className="bg-emerald-500 h-2 rounded-l-full absolute top-0 left-0 transition-all duration-500" style={{ width: `${barPct * (cpv.win_rate / 100)}%` }}></div>
                </div>
              </div>
            );
          })}
        </div>
        <div className="flex gap-4 mt-3 text-[10px] text-slate-400 border-t border-slate-100 pt-2">
          <span className="flex items-center gap-1"><div className="w-3 h-1.5 rounded bg-emerald-500"></div> Admise/Parțial admise</span>
          <span className="flex items-center gap-1"><div className="w-3 h-1.5 rounded bg-slate-300"></div> Total decizii (scală relativă)</span>
          <span className="ml-auto">* = grupare pe primele 3 cifre CPV</span>
        </div>
      </div>
      )}

      {/* Fallback: old CPV top if grouped not available */}
      {cpvTopGrouped.length === 0 && cpvTopStats.length > 0 && (
      <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm mb-6">
        <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2">
          <BarChart3 size={18} className="text-purple-500" />
          Top 10 Coduri CPV
        </h3>
        <div className="space-y-2">
          {cpvTopStats.map((cpv) => {
            const maxCount = cpvTopStats[0]?.count || 1;
            const pct = Math.round((cpv.count / maxCount) * 100);
            return (
              <button key={cpv.code} onClick={() => { setFilterCpv([cpv.code]); setMode('datalake'); }} className="w-full text-left group hover:bg-slate-50 rounded-lg px-2 py-1.5 transition">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-xs font-mono font-bold text-slate-600 w-24 shrink-0">{cpv.code}</span>
                  <span className="text-xs text-slate-400 truncate flex-1">{cpv.description || ''}</span>
                  <span className="text-xs font-semibold text-slate-600 tabular-nums shrink-0">{cpv.count}</span>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-1.5">
                  <div className="bg-purple-500 h-1.5 rounded-full transition-all duration-500" style={{ width: `${pct}%` }}></div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
      )}

      {/* Row 3: Win rate by criticism code — two columns: DOC vs REZ */}
      {winRateByCritici.length > 0 && (() => {
        const docCritici = winRateByCritici.filter((cr: any) => cr.code.startsWith('D'));
        const rezCritici = winRateByCritici.filter((cr: any) => cr.code.startsWith('R'));
        const CriticaCard = ({ cr }: { cr: any }) => {
          const legend = CRITIQUE_LEGEND[cr.code] || `Critică ${cr.code}`;
          const winPct = cr.contestator_win_rate;
          const winColor = winPct >= 60 ? 'text-emerald-600' : winPct >= 40 ? 'text-amber-600' : 'text-red-600';
          const barColor = winPct >= 60 ? 'bg-emerald-500' : winPct >= 40 ? 'bg-amber-500' : 'bg-red-500';
          const bgColor = winPct >= 60 ? 'bg-emerald-50' : winPct >= 40 ? 'bg-amber-50' : 'bg-red-50';
          return (
            <button
              key={cr.code}
              onClick={() => { setFilterCritici([cr.code]); setMode('datalake'); }}
              className={`${bgColor} border border-slate-100 rounded-lg p-3 text-left hover:shadow-md transition w-full`}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-bold text-slate-700">{cr.code}</span>
                <span className="text-[10px] text-slate-400">{cr.total} cazuri</span>
              </div>
              <p className="text-[11px] text-slate-600 line-clamp-2 mb-2 leading-relaxed" title={legend}>{legend}</p>
              <div className="flex items-center gap-2">
                <div className="flex-1 bg-white/60 rounded-full h-1.5">
                  <div className={`${barColor} h-1.5 rounded-full transition-all duration-500`} style={{ width: `${winPct}%` }}></div>
                </div>
                <span className={`text-xs font-bold ${winColor} tabular-nums`}>{winPct}%</span>
              </div>
            </button>
          );
        };

        return (
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm mb-6">
          <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2">
            <Shield size={18} className="text-indigo-500" />
            Rata de Succes pe Critici (sortare după volum)
          </h3>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* DOCUMENTAȚIE column */}
            <div>
              <div className="flex items-center gap-2 mb-3 pb-2 border-b border-purple-200">
                <span className="bg-purple-100 text-purple-700 px-2.5 py-1 rounded-md text-xs font-bold">D</span>
                <span className="text-sm font-bold text-purple-800">DOCUMENTAȚIE</span>
                <span className="text-[10px] text-slate-400 ml-auto">{docCritici.reduce((s: number, c: any) => s + c.total, 0)} total</span>
              </div>
              <div className="space-y-2">
                {docCritici.map((cr: any) => <CriticaCard key={cr.code} cr={cr} />)}
              </div>
            </div>
            {/* REZULTAT column */}
            <div>
              <div className="flex items-center gap-2 mb-3 pb-2 border-b border-orange-200">
                <span className="bg-orange-100 text-orange-700 px-2.5 py-1 rounded-md text-xs font-bold">R</span>
                <span className="text-sm font-bold text-orange-800">REZULTAT</span>
                <span className="text-[10px] text-slate-400 ml-auto">{rezCritici.reduce((s: number, c: any) => s + c.total, 0)} total</span>
              </div>
              <div className="space-y-2">
                {rezCritici.map((cr: any) => <CriticaCard key={cr.code} cr={cr} />)}
              </div>
            </div>
          </div>
          <div className="mt-3 text-[10px] text-slate-400 border-t border-slate-100 pt-2 text-right">
            % = rata de admitere contestator (admis + parțial)
          </div>
        </div>
        );
      })()}

      {/* Quick navigation */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <button onClick={() => setMode('chat')} className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:border-blue-200 transition text-left group">
          <MessageSquare size={20} className="text-blue-500 mb-2 group-hover:scale-110 transition-transform" />
          <h4 className="font-bold text-slate-800 text-sm">Asistent AP</h4>
          <p className="text-xs text-slate-500 mt-1">Întreabă despre jurisprudență CNSC</p>
        </button>
        <button onClick={() => setMode('datalake')} className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:border-purple-200 transition text-left group">
          <Filter size={20} className="text-purple-500 mb-2 group-hover:scale-110 transition-transform" />
          <h4 className="font-bold text-slate-800 text-sm">Decizii CNSC</h4>
          <p className="text-xs text-slate-500 mt-1">Explorează și filtrează decizii CNSC</p>
        </button>
        <button onClick={() => setMode('training')} className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:border-amber-200 transition text-left group">
          <GraduationCap size={20} className="text-amber-500 mb-2 group-hover:scale-110 transition-transform" />
          <h4 className="font-bold text-slate-800 text-sm">TrainingAP</h4>
          <p className="text-xs text-slate-500 mt-1">Generează materiale didactice</p>
        </button>
      </div>
    </div>
    </div>
    );
  };

  const closeAllDropdowns = () => { setShowRulingDropdown(false); setShowYearDropdown(false); setShowCriticiDropdown(false); setShowCpvDropdown(false); setShowCategorieDropdown(false); setShowClasaDropdown(false); setShowMotivDropdown(false); setShowCompletDropdown(false); setShowDomeniuDropdown(false); setShowTipProceduraDropdown(false); setShowCriteriuAtribuireDropdown(false); };

  const domeniuLabels: Record<string, string> = {
    'achizitii_publice': 'Achiziții publice',
    'achizitii_sectoriale': 'Achiziții sectoriale',
    'concesiuni': 'Concesiuni',
  };

  const tipProceduraLabels: Record<string, string> = {
    'licitatie_deschisa': 'Licitație deschisă',
    'licitatie_restransa': 'Licitație restrânsă',
    'negociere_competitiva': 'Negociere competitivă',
    'negociere_fara_publicare': 'Negociere fără publicare',
    'negociere_fara_invitatie': 'Negociere fără invitație',
    'negociere_fara_anunt': 'Negociere fără anunț',
    'dialog_competitiv': 'Dialog competitiv',
    'parteneriat_inovare': 'Parteneriat pentru inovare',
    'concurs_solutii': 'Concurs de soluții',
    'servicii_sociale': 'Servicii sociale',
    'procedura_simplificata': 'Procedură simplificată',
  };

  const renderDataLake = () => {
    const totalDecisions = dbStats?.total_decisions || 0;
    const documentatieCount = dbStats?.by_type?.['documentatie'] || 0;
    const rezultatCount = dbStats?.by_type?.['rezultat'] || 0;
    const totalPages = Math.ceil(apiDecisionsTotal / 20);

    const goToPage = (page: number) => {
      if (page >= 1 && page <= totalPages) {
        fetchDecisions(page, fileSearch);
      }
    };

    // Ruling badge helper
    const rulingBadge = (solutie: string | null) => {
      const label = solutie === 'ADMIS' ? 'Admis' :
                    solutie === 'ADMIS_PARTIAL' ? 'Admis Parțial' :
                    solutie === 'RESPINS' ? 'Respins' : solutie || 'N/A';
      const cls = solutie === 'ADMIS' ? 'bg-emerald-500 text-white' :
                  solutie === 'ADMIS_PARTIAL' ? 'bg-amber-500 text-white' :
                  solutie === 'RESPINS' ? 'bg-red-500 text-white' :
                  'bg-slate-400 text-white';
      return <span className={`text-[11px] px-2.5 py-0.5 rounded-full font-semibold tracking-wide ${cls}`}>{label}</span>;
    };

    return (
      <div className="h-full flex flex-col bg-slate-50/80">
        {/* Header + Stats row */}
        <div className="px-4 md:px-6 pt-4 md:pt-5 pb-3 md:pb-4 bg-white border-b border-slate-200 shrink-0">
          <div className="flex items-center gap-3 md:gap-4 flex-wrap">
            <div className="shrink-0">
              <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2">
                <Filter className="text-blue-600" size={22}/> Decizii CNSC
              </h2>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex items-center gap-1.5 bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full border border-emerald-200 text-[11px] font-medium">
                  <Wifi size={11} />
                  Conectat
                </div>
                <span className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-blue-50 text-blue-600 border border-blue-200">
                  PostgreSQL
                </span>
                <button
                  onClick={() => { setShowImportModal(true); setImportResult(null); }}
                  className="flex items-center gap-1 bg-indigo-50 text-indigo-700 px-2.5 py-0.5 rounded-full border border-indigo-200 text-[11px] font-medium hover:bg-indigo-100 transition cursor-pointer"
                >
                  <Upload size={11} />
                  Import
                </button>
              </div>
            </div>

            {/* Stats Cards — pushed to right */}
            <div className="flex items-center gap-2 ml-auto min-w-0 overflow-x-auto">
              <div className="bg-gradient-to-br from-blue-50 to-blue-100/50 rounded-lg px-3 py-1.5 md:px-4 md:py-3 border border-blue-200/60 shrink-0">
                <div className="text-sm md:text-2xl font-extrabold text-blue-700 tracking-tight">{totalDecisions.toLocaleString()}</div>
                <div className="text-[9px] md:text-[11px] text-blue-600/80 font-medium">Total Decizii</div>
              </div>
              <div className="bg-gradient-to-br from-purple-50 to-purple-100/50 rounded-lg px-3 py-1.5 md:px-4 md:py-3 border border-purple-200/60 shrink-0">
                <div className="text-sm md:text-2xl font-extrabold text-purple-700 tracking-tight">{documentatieCount.toLocaleString()}</div>
                <div className="text-[9px] md:text-[11px] text-purple-600/80 font-medium">Documentație</div>
              </div>
              <div className="bg-gradient-to-br from-orange-50 to-orange-100/50 rounded-lg px-3 py-1.5 md:px-4 md:py-3 border border-orange-200/60 shrink-0">
                <div className="text-sm md:text-2xl font-extrabold text-orange-700 tracking-tight">{rezultatCount.toLocaleString()}</div>
                <div className="text-[9px] md:text-[11px] text-orange-600/80 font-medium">Rezultat</div>
              </div>
              <div className="bg-gradient-to-br from-emerald-50 to-emerald-100/50 rounded-lg px-3 py-1.5 md:px-4 md:py-3 border border-emerald-200/60 shrink-0">
                <div className="text-sm md:text-2xl font-extrabold text-emerald-700 tracking-tight">{dbStats?.last_updated ? new Date(dbStats.last_updated).toLocaleDateString('ro-RO') : '-'}</div>
                <div className="text-[9px] md:text-[11px] text-emerald-600/80 font-medium">Ultima actualizare</div>
              </div>
            </div>
          </div>
        </div>

        {/* Search Bar — prominent, own row */}
        <div className="px-4 md:px-6 pt-3 pb-2 bg-white shrink-0">
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
            <input
              type="text"
              className="w-full pl-12 pr-4 py-3 md:py-2.5 border-2 border-slate-300 rounded-xl text-base md:text-sm bg-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 focus:shadow-inner outline-none transition placeholder:text-slate-400"
              placeholder="Caută după număr decizie, autoritate, CPV..."
              value={fileSearch}
              onChange={(e) => setFileSearch(e.target.value)}
            />
          </div>
        </div>

        {/* Filters Bar */}
        <div className="px-4 md:px-6 py-2 border-b border-slate-200 bg-white shrink-0 space-y-2">
          {/* Row 1: Domeniu, Tip, Soluție, Motiv resp.*, Critici, Categorie CPV, Clasă, CPV */}
          <div className="flex items-center gap-2 flex-wrap">

              {/* Domeniu Legislativ Multi-select */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowDomeniuDropdown(!showDomeniuDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterDomeniu.length > 0 ? 'border-indigo-400 bg-indigo-50 text-indigo-700' : 'border-slate-300'}`}
                >
                  Domeniu{filterDomeniu.length > 0 ? ` (${filterDomeniu.length})` : ''}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showDomeniuDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-56 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    {domeniuOptions.map(opt => {
                      const isSelected = filterDomeniu.includes(opt.name);
                      const label = opt.name === 'achizitii_publice' ? 'Achiziții publice' : opt.name === 'achizitii_sectoriale' ? 'Achiziții sectoriale' : opt.name === 'concesiuni' ? 'Concesiuni' : opt.name;
                      return (
                        <button key={opt.name} onClick={(e) => { e.stopPropagation(); setFilterDomeniu(prev => isSelected ? prev.filter(c => c !== opt.name) : [...prev, opt.name]); }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2">
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="text-slate-700">{label}</span>
                          <span className="text-slate-300 tabular-nums ml-auto">{opt.count}</span>
                        </button>
                      );
                    })}
                    {filterDomeniu.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterDomeniu([])} className="text-xs text-indigo-600 hover:text-indigo-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Soluție Multi-select Dropdown */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowRulingDropdown(!showRulingDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterRuling.length > 0 ? 'border-green-400 bg-green-50 text-green-700' : 'border-slate-300'}`}
                >
                  Soluție{filterRuling.length > 0 ? ` (${filterRuling.length})` : ': Toate'}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showRulingDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-56 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    {[
                      { code: "ADMIS", label: "Admis" },
                      { code: "ADMIS_PARTIAL", label: "Admis Parțial" },
                      { code: "RESPINS", label: "Respins" },
                      { code: "__NULL__", label: "Fără soluție" },
                    ].map(opt => {
                      const isSelected = filterRuling.includes(opt.code);
                      return (
                        <button
                          key={opt.code}
                          onClick={(e) => {
                            e.stopPropagation();
                            setFilterRuling(prev => isSelected ? prev.filter(c => c !== opt.code) : [...prev, opt.code]);
                          }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2"
                        >
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-green-600 border-green-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="text-slate-700">{opt.label}</span>
                        </button>
                      );
                    })}
                    {filterRuling.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterRuling([])} className="text-xs text-green-600 hover:text-green-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Motiv Respingere — only when RESPINS is selected */}
              {filterRuling.includes('RESPINS') && (
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowMotivDropdown(!showMotivDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterMotivRespingere.length > 0 ? 'border-red-400 bg-red-50 text-red-700' : 'border-slate-300'}`}
                >
                  Motiv{filterMotivRespingere.length > 0 ? ` (${filterMotivRespingere.length})` : ''}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showMotivDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-56 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    {motivRespingereOptions.map(opt => {
                      const isSelected = filterMotivRespingere.includes(opt.name);
                      return (
                        <button key={opt.name} onClick={(e) => { e.stopPropagation(); setFilterMotivRespingere(prev => isSelected ? prev.filter(c => c !== opt.name) : [...prev, opt.name]); }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2">
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-red-600 border-red-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="text-slate-700 capitalize">{opt.name}</span>
                          <span className="text-slate-300 tabular-nums ml-auto">{opt.count}</span>
                        </button>
                      );
                    })}
                    {filterMotivRespingere.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterMotivRespingere([])} className="text-xs text-red-600 hover:text-red-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>
              )}

              <div className="relative flex-1 min-w-[100px]">
                <select value={filterType} onChange={(e) => setFilterType(e.target.value)} className="appearance-none text-xs border border-slate-300 rounded-lg pl-3 pr-7 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition w-full cursor-pointer">
                  <option value="">Tip: Toate</option>
                  <option value="documentatie">Documentație</option>
                  <option value="rezultat">Rezultat</option>
                </select>
                <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              </div>
              {/* Categorie Dropdown (Furnizare/Servicii/Lucrări) */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowCategorieDropdown(!showCategorieDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterCategorie ? 'border-orange-400 bg-orange-50 text-orange-700' : 'border-slate-300'}`}
                >
                  <Package size={12} />
                  {filterCategorie || 'Categorie'}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showCategorieDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-56 py-1" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={(e) => { e.stopPropagation(); setFilterCategorie(""); setFilterClasa(""); setShowCategorieDropdown(false); }}
                      className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 text-slate-500"
                    >
                      Toate categoriile
                    </button>
                    {categoriiOptions.map(opt => (
                      <button
                        key={opt.name}
                        onClick={(e) => { e.stopPropagation(); setFilterCategorie(opt.name); setFilterClasa(""); setShowCategorieDropdown(false); }}
                        className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center justify-between ${filterCategorie === opt.name ? 'bg-orange-50 text-orange-700 font-semibold' : ''}`}
                      >
                        <span>{opt.name}</span>
                        <span className="text-slate-300 tabular-nums">{opt.count}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Clasă Produse Dropdown (depends on Categorie) */}
              {(filterCategorie || claseOptions.length > 0) && (
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowClasaDropdown(!showClasaDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterClasa ? 'border-teal-400 bg-teal-50 text-teal-700' : 'border-slate-300'}`}
                >
                  <Layers size={12} />
                  <span className="truncate">{filterClasa || 'Clasă'}</span>
                  <ChevronDown size={12} className="ml-auto shrink-0" />
                </button>
                {showClasaDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-72 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={(e) => { e.stopPropagation(); setFilterClasa(""); setShowClasaDropdown(false); }}
                      className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 text-slate-500"
                    >
                      Toate clasele
                    </button>
                    {claseOptions.map(opt => (
                      <button
                        key={opt.name}
                        onClick={(e) => { e.stopPropagation(); setFilterClasa(opt.name); setShowClasaDropdown(false); }}
                        className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center justify-between gap-2 ${filterClasa === opt.name ? 'bg-teal-50 text-teal-700 font-semibold' : ''}`}
                      >
                        <span className="truncate">{opt.name}</span>
                        <span className="text-slate-300 tabular-nums shrink-0">{opt.count}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              )}

              {/* Year Multi-select Dropdown */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowYearDropdown(!showYearDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterYears.length > 0 ? 'border-blue-400 bg-blue-50 text-blue-700' : 'border-slate-300'}`}
                >
                  An{filterYears.length > 0 ? `: ${filterYears.join(', ')}` : ': Toate'}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showYearDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-40 py-1" onClick={(e) => e.stopPropagation()}>
                    {Array.from({length: 7}, (_, i) => String(2026 - i)).map(y => {
                      const isSelected = filterYears.includes(y);
                      return (
                        <button
                          key={y}
                          onClick={(e) => {
                            e.stopPropagation();
                            setFilterYears(prev => isSelected ? prev.filter(x => x !== y) : [...prev, y]);
                          }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2"
                        >
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-blue-600 border-blue-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="font-medium text-slate-700">{y}</span>
                        </button>
                      );
                    })}
                    {filterYears.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterYears([])} className="text-xs text-blue-600 hover:text-blue-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Critici Multi-select Dropdown */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowCriticiDropdown(!showCriticiDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterCritici.length > 0 ? 'border-blue-400 bg-blue-50 text-blue-700' : 'border-slate-300'}`}
                >
                  <Filter size={12} />
                  Critici{filterCritici.length > 0 && ` (${filterCritici.length})`}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showCriticiDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-64 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    {criticiOptions.map(opt => {
                      const isSelected = filterCritici.includes(opt.code);
                      const legend = (CRITIQUE_LEGEND as any)[opt.code] || '';
                      return (
                        <button
                          key={opt.code}
                          onClick={(e) => {
                            e.stopPropagation();
                            setFilterCritici(prev => isSelected ? prev.filter(c => c !== opt.code) : [...prev, opt.code]);
                          }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2"
                        >
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-blue-600 border-blue-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="font-mono font-bold text-slate-700">{opt.code}</span>
                          <span className="text-slate-400 truncate flex-1">{legend}</span>
                          <span className="text-slate-300 tabular-nums">{opt.count}</span>
                        </button>
                      );
                    })}
                    {filterCritici.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterCritici([])} className="text-xs text-blue-600 hover:text-blue-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* CPV Multi-select Dropdown with Search */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowCpvDropdown(!showCpvDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterCpv.length > 0 ? 'border-blue-400 bg-blue-50 text-blue-700' : 'border-slate-300'}`}
                >
                  <Filter size={12} />
                  CPV{filterCpv.length > 0 && ` (${filterCpv.length})`}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showCpvDropdown && (
                  <div className="absolute top-full right-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-96 max-h-96 flex flex-col" onClick={(e) => e.stopPropagation()}>
                    <div className="p-2 border-b border-slate-100 shrink-0">
                      <div className="relative">
                        <input
                          type="text"
                          placeholder="Caută cod CPV sau descriere..."
                          value={cpvSearchTerm}
                          onChange={(e) => setCpvSearchTerm(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                          className="w-full text-xs border border-slate-200 rounded px-2.5 py-1.5 pr-7 focus:ring-2 focus:ring-blue-500/40 outline-none"
                        />
                        {cpvSearchTerm && (
                          <button
                            onClick={(e) => { e.stopPropagation(); setCpvSearchTerm(""); }}
                            className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                          >
                            <X size={12} />
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="overflow-y-auto flex-1 py-1">
                      {/* Tree view when no search, flat list when searching */}
                      {!cpvSearchTerm.trim() && cpvTree.length > 0 ? (
                        cpvTree.map((div: any) => {
                          const isExpanded = cpvTreeExpanded.has(div.code);
                          const divSelected = div.children?.some((c: any) => filterCpv.includes(c.code));
                          return (
                            <div key={div.code}>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setCpvTreeExpanded(prev => {
                                    const next = new Set(prev);
                                    if (next.has(div.code)) next.delete(div.code);
                                    else next.add(div.code);
                                    return next;
                                  });
                                }}
                                className={`w-full text-left px-3 py-2 text-xs hover:bg-slate-50 flex items-center gap-2 ${divSelected ? 'bg-purple-50' : ''}`}
                              >
                                {isExpanded ? <ChevronDown size={12} className="shrink-0 text-slate-400" /> : <ChevronRight size={12} className="shrink-0 text-slate-400" />}
                                <span className="font-mono font-bold text-slate-600 shrink-0">{div.code}</span>
                                <span className="text-slate-500 truncate flex-1">{div.description || div.categorie || ''}</span>
                                <span className="text-slate-300 tabular-nums shrink-0 font-medium">{div.count}</span>
                              </button>
                              {isExpanded && div.children?.map((child: any) => {
                                const isSelected = filterCpv.includes(child.code);
                                return (
                                  <button
                                    key={child.code}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setFilterCpv(prev => isSelected ? prev.filter(c => c !== child.code) : [...prev, child.code]);
                                    }}
                                    className="w-full text-left pl-8 pr-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2"
                                  >
                                    <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-purple-600 border-purple-600 text-white' : 'border-slate-300'}`}>
                                      {isSelected && <CheckSquare size={9} />}
                                    </span>
                                    <span className="font-mono text-slate-600 shrink-0">{child.code}</span>
                                    <span className="text-slate-400 truncate flex-1">{child.description || ''}</span>
                                    <span className="text-slate-300 tabular-nums shrink-0">{child.count}</span>
                                  </button>
                                );
                              })}
                            </div>
                          );
                        })
                      ) : (
                        cpvOptions.map(opt => {
                          const isSelected = filterCpv.includes(opt.code);
                          return (
                            <button
                              key={opt.code}
                              onClick={(e) => {
                                e.stopPropagation();
                                setFilterCpv(prev => isSelected ? prev.filter(c => c !== opt.code) : [...prev, opt.code]);
                              }}
                              className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2"
                            >
                              <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-purple-600 border-purple-600 text-white' : 'border-slate-300'}`}>
                                {isSelected && <CheckSquare size={11} />}
                              </span>
                              <span className="font-mono font-semibold text-slate-700 shrink-0">{opt.code}</span>
                              <span className="text-slate-400 truncate flex-1">{opt.description || ''}</span>
                              <span className="text-slate-300 tabular-nums shrink-0">{opt.count}</span>
                            </button>
                          );
                        })
                      )}
                    </div>
                    {filterCpv.length > 0 && (
                      <div className="border-t border-slate-100 px-3 py-1.5 shrink-0">
                        <button onClick={() => setFilterCpv([])} className="text-xs text-purple-600 hover:text-purple-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>
          </div>

          {/* Row 2: Tip procedură, Criteriu atribuire, Complet, VEA, Date range */}
          <div className="flex items-center gap-2 flex-wrap">

              {/* Tip Procedură Multi-select */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowTipProceduraDropdown(!showTipProceduraDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterTipProcedura.length > 0 ? 'border-amber-400 bg-amber-50 text-amber-700' : 'border-slate-300'}`}
                >
                  Procedură{filterTipProcedura.length > 0 ? ` (${filterTipProcedura.length})` : ''}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showTipProceduraDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-72 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    {tipProceduraOptions.map(opt => {
                      const isSelected = filterTipProcedura.includes(opt.name);
                      const labels: Record<string, string> = { licitatie_deschisa: 'Licitație deschisă', licitatie_restransa: 'Licitație restrânsă', negociere_competitiva: 'Negociere competitivă', negociere_fara_publicare: 'Negociere fără publicare', negociere_fara_invitatie: 'Negociere fără invitație', negociere_fara_anunt: 'Negociere fără anunț', dialog_competitiv: 'Dialog competitiv', parteneriat_inovare: 'Parteneriat pt. inovare', concurs_solutii: 'Concurs de soluții', servicii_sociale: 'Servicii sociale', procedura_simplificata: 'Procedură simplificată' };
                      return (
                        <button key={opt.name} onClick={(e) => { e.stopPropagation(); setFilterTipProcedura(prev => isSelected ? prev.filter(c => c !== opt.name) : [...prev, opt.name]); }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2">
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-amber-600 border-amber-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="text-slate-700">{labels[opt.name] || opt.name}</span>
                          <span className="text-slate-300 tabular-nums ml-auto">{opt.count}</span>
                        </button>
                      );
                    })}
                    {filterTipProcedura.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterTipProcedura([])} className="text-xs text-amber-600 hover:text-amber-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Criteriu Atribuire Multi-select */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowCriteriuAtribuireDropdown(!showCriteriuAtribuireDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterCriteriuAtribuire.length > 0 ? 'border-cyan-400 bg-cyan-50 text-cyan-700' : 'border-slate-300'}`}
                >
                  Criteriu{filterCriteriuAtribuire.length > 0 ? ` (${filterCriteriuAtribuire.length})` : ''}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showCriteriuAtribuireDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-72 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    {criteriuAtribuireOptions.map(opt => {
                      const isSelected = filterCriteriuAtribuire.includes(opt.name);
                      return (
                        <button key={opt.name} onClick={(e) => { e.stopPropagation(); setFilterCriteriuAtribuire(prev => isSelected ? prev.filter(c => c !== opt.name) : [...prev, opt.name]); }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2">
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-cyan-600 border-cyan-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="text-slate-700">{opt.name}</span>
                          <span className="text-slate-300 tabular-nums ml-auto">{opt.count}</span>
                        </button>
                      );
                    })}
                    {filterCriteriuAtribuire.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterCriteriuAtribuire([])} className="text-xs text-cyan-600 hover:text-cyan-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Complet Multi-select (C1-C20) */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); closeAllDropdowns(); setShowCompletDropdown(!showCompletDropdown); }}
                  className={`text-xs border rounded-lg px-3 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 outline-none transition w-full cursor-pointer flex items-center gap-1.5 ${filterComplet.length > 0 ? 'border-slate-500 bg-slate-50 text-slate-700' : 'border-slate-300'}`}
                >
                  Complet{filterComplet.length > 0 ? ` (${filterComplet.length})` : ''}
                  <ChevronDown size={12} className="ml-auto" />
                </button>
                {showCompletDropdown && (
                  <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 w-48 max-h-72 overflow-y-auto py-1" onClick={(e) => e.stopPropagation()}>
                    {completOptions.map(opt => {
                      const isSelected = filterComplet.includes(opt.name);
                      return (
                        <button key={opt.name} onClick={(e) => { e.stopPropagation(); setFilterComplet(prev => isSelected ? prev.filter(c => c !== opt.name) : [...prev, opt.name]); }}
                          className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 flex items-center gap-2">
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected ? 'bg-slate-600 border-slate-600 text-white' : 'border-slate-300'}`}>
                            {isSelected && <CheckSquare size={11} />}
                          </span>
                          <span className="font-mono font-semibold text-slate-700">{opt.name}</span>
                          <span className="text-slate-300 tabular-nums ml-auto">{opt.count}</span>
                        </button>
                      );
                    })}
                    {filterComplet.length > 0 && (
                      <div className="border-t border-slate-100 mt-1 pt-1 px-3 pb-1">
                        <button onClick={() => setFilterComplet([])} className="text-xs text-slate-600 hover:text-slate-800">Șterge selecția</button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* VEA (Valoare Estimată) min-max */}
              <div className="flex items-center gap-1.5 shrink-0">
                <span className="text-[10px] text-slate-500 font-medium">VEA</span>
                <input type="number" placeholder="min" value={filterValoareMin} onChange={(e) => setFilterValoareMin(e.target.value)}
                  className="w-20 text-xs border border-slate-300 rounded-lg px-2 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition" />
                <span className="text-slate-400 text-xs">—</span>
                <input type="number" placeholder="max" value={filterValoareMax} onChange={(e) => setFilterValoareMax(e.target.value)}
                  className="w-20 text-xs border border-slate-300 rounded-lg px-2 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition" />
                <span className="text-[10px] text-slate-400">RON</span>
              </div>

              {/* Date Range */}
              <div className="flex items-center gap-1.5 shrink-0">
                <span className="text-[10px] text-slate-500 font-medium">Perioadă</span>
                <input type="date" value={filterDateFrom} onChange={(e) => setFilterDateFrom(e.target.value)}
                  className="text-xs border border-slate-300 rounded-lg px-2 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition" />
                <span className="text-slate-400 text-xs">—</span>
                <input type="date" value={filterDateTo} onChange={(e) => setFilterDateTo(e.target.value)}
                  className="text-xs border border-slate-300 rounded-lg px-2 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition" />
              </div>

          </div>

          {/* Action bar + filter pills */}
          {(filterRuling.length > 0 || filterType || filterYears.length > 0 || fileSearch || filterCritici.length > 0 || filterCpv.length > 0 || filterCategorie || filterClasa || filterMotivRespingere.length > 0 || filterComplet.length > 0 || filterDomeniu.length > 0 || filterTipProcedura.length > 0 || filterCriteriuAtribuire.length > 0 || filterDateFrom || filterDateTo || filterValoareMin || filterValoareMax || scopes.length > 0) && (
          <div className="flex items-center gap-1.5 flex-wrap border-t border-slate-100 pt-2">
              {/* Filter pills */}
              {filterDomeniu.map(d => {
                const label = d === 'achizitii_publice' ? 'Ach. publice' : d === 'achizitii_sectoriale' ? 'Ach. sectoriale' : d === 'concesiuni' ? 'Concesiuni' : d;
                return (
                  <span key={d} className="text-[10px] bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-full px-2 py-0.5 flex items-center gap-1 font-semibold">
                    {label}
                    <button onClick={() => setFilterDomeniu(prev => prev.filter(x => x !== d))} className="hover:text-red-500"><X size={10} /></button>
                  </span>
                );
              })}
              {filterRuling.map(r => (
                <span key={r} className="text-[10px] bg-green-50 text-green-700 border border-green-200 rounded-full px-2 py-0.5 flex items-center gap-1 font-semibold">
                  {r === '__NULL__' ? 'Fără soluție' : r}
                  <button onClick={() => { setFilterRuling(prev => prev.filter(x => x !== r)); if (r === 'RESPINS') setFilterMotivRespingere([]); }} className="hover:text-red-500"><X size={10} /></button>
                </span>
              ))}
              {filterMotivRespingere.map(m => (
                <span key={m} className="text-[10px] bg-red-50 text-red-700 border border-red-200 rounded-full px-2 py-0.5 flex items-center gap-1 capitalize">
                  {m}
                  <button onClick={() => setFilterMotivRespingere(prev => prev.filter(x => x !== m))} className="hover:text-red-500"><X size={10} /></button>
                </span>
              ))}
              {filterType && (
                <span className="text-[10px] bg-purple-50 text-purple-700 border border-purple-200 rounded-full px-2 py-0.5 flex items-center gap-1">
                  {filterType === 'documentatie' ? 'Documentație' : 'Rezultat'}
                  <button onClick={() => setFilterType("")} className="hover:text-red-500"><X size={10} /></button>
                </span>
              )}
              {filterCategorie && (
                <span className="text-[10px] bg-orange-50 text-orange-700 border border-orange-200 rounded-full px-2 py-0.5 flex items-center gap-1 font-semibold">
                  {filterCategorie}
                  <button onClick={() => { setFilterCategorie(""); setFilterClasa(""); }} className="hover:text-red-500"><X size={10} /></button>
                </span>
              )}
              {filterClasa && (
                <span className="text-[10px] bg-teal-50 text-teal-700 border border-teal-200 rounded-full px-2 py-0.5 flex items-center gap-1 truncate max-w-[200px]">
                  {filterClasa}
                  <button onClick={() => setFilterClasa("")} className="hover:text-red-500 shrink-0"><X size={10} /></button>
                </span>
              )}
              {filterYears.map(y => (
                <span key={y} className="text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-2 py-0.5 flex items-center gap-1 font-semibold">
                  {y}
                  <button onClick={() => setFilterYears(prev => prev.filter(x => x !== y))} className="hover:text-red-500"><X size={10} /></button>
                </span>
              ))}
              {filterCritici.map(c => (
                <span key={c} className="text-[10px] bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2 py-0.5 flex items-center gap-1 font-mono font-semibold">
                  {c}
                  <button onClick={() => setFilterCritici(prev => prev.filter(x => x !== c))} className="hover:text-red-500"><X size={10} /></button>
                </span>
              ))}
              {filterCpv.map(c => (
                <span key={c} className="text-[10px] bg-purple-50 text-purple-700 border border-purple-200 rounded-full px-2 py-0.5 flex items-center gap-1 font-mono">
                  {c}
                  <button onClick={() => setFilterCpv(prev => prev.filter(x => x !== c))} className="hover:text-red-500"><X size={10} /></button>
                </span>
              ))}
              {filterTipProcedura.map(p => {
                const labels: Record<string, string> = { licitatie_deschisa: 'Licit. deschisă', licitatie_restransa: 'Licit. restrânsă', negociere_competitiva: 'Neg. competitivă', negociere_fara_publicare: 'Neg. fără pub.', negociere_fara_invitatie: 'Neg. fără inv.', negociere_fara_anunt: 'Neg. fără anunț', dialog_competitiv: 'Dialog comp.', parteneriat_inovare: 'Part. inovare', concurs_solutii: 'Concurs sol.', servicii_sociale: 'Serv. sociale', procedura_simplificata: 'Proc. simplificată' };
                return (
                  <span key={p} className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 rounded-full px-2 py-0.5 flex items-center gap-1">
                    {labels[p] || p}
                    <button onClick={() => setFilterTipProcedura(prev => prev.filter(x => x !== p))} className="hover:text-red-500"><X size={10} /></button>
                  </span>
                );
              })}
              {filterCriteriuAtribuire.map(c => (
                <span key={c} className="text-[10px] bg-cyan-50 text-cyan-700 border border-cyan-200 rounded-full px-2 py-0.5 flex items-center gap-1">
                  {c}
                  <button onClick={() => setFilterCriteriuAtribuire(prev => prev.filter(x => x !== c))} className="hover:text-red-500"><X size={10} /></button>
                </span>
              ))}
              {filterComplet.map(c => (
                <span key={c} className="text-[10px] bg-slate-100 text-slate-700 border border-slate-300 rounded-full px-2 py-0.5 flex items-center gap-1 font-mono">
                  {c}
                  <button onClick={() => setFilterComplet(prev => prev.filter(x => x !== c))} className="hover:text-red-500"><X size={10} /></button>
                </span>
              ))}
              {(filterDateFrom || filterDateTo) && (
                <span className="text-[10px] bg-slate-50 text-slate-600 border border-slate-200 rounded-full px-2 py-0.5 flex items-center gap-1">
                  {filterDateFrom || '...'} — {filterDateTo || '...'}
                  <button onClick={() => { setFilterDateFrom(""); setFilterDateTo(""); }} className="hover:text-red-500"><X size={10} /></button>
                </span>
              )}
              {(filterValoareMin || filterValoareMax) && (
                <span className="text-[10px] bg-slate-50 text-slate-600 border border-slate-200 rounded-full px-2 py-0.5 flex items-center gap-1">
                  VEA: {filterValoareMin || '0'} — {filterValoareMax || '∞'} RON
                  <button onClick={() => { setFilterValoareMin(""); setFilterValoareMax(""); }} className="hover:text-red-500"><X size={10} /></button>
                </span>
              )}

              {/* Action buttons — right-aligned */}
              <div className="ml-auto flex items-center gap-2 shrink-0">
                <button onClick={() => { setFilterRuling([]); setFilterType(""); setFilterYears([]); setFileSearch(""); setFilterCritici([]); setFilterCpv([]); setFilterCategorie(""); setFilterClasa(""); setFilterMotivRespingere([]); setFilterComplet([]); setFilterDomeniu([]); setFilterTipProcedura([]); setFilterCriteriuAtribuire([]); setFilterDateFrom(""); setFilterDateTo(""); setFilterValoareMin(""); setFilterValoareMax(""); setEditingScopeFilters(null); }}
                  className="text-xs text-red-500 hover:text-red-700 font-medium whitespace-nowrap flex items-center gap-1 transition">
                  <X size={13} /> Resetează
                </button>
                <button onClick={() => setShowScopeModal(true)}
                  className="text-xs bg-blue-600 text-white hover:bg-blue-700 font-medium whitespace-nowrap flex items-center gap-1.5 transition px-3 py-1.5 rounded-lg shadow-sm">
                  <Bookmark size={13} /> Salvează
                </button>
                {scopes.length > 0 && (
                  <button onClick={() => setShowScopeManager(true)}
                    className="text-xs border border-slate-300 text-slate-600 hover:border-blue-400 hover:text-blue-600 font-medium whitespace-nowrap flex items-center gap-1.5 transition px-3 py-1.5 rounded-lg">
                    <Pencil size={12} /> Editează filtre
                  </button>
                )}
              </div>
          </div>
          )}
        </div>

        {/* Scope filter editing banner — UPDATE filter save to include new filters */}
        {editingScopeFilters && (
          <div className="mx-4 md:mx-6 mt-3 flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-4 py-2.5 text-xs text-amber-800">
            <Pencil size={13} className="shrink-0" />
            <span>Editezi filtrele scope-ului <strong>{scopes.find(s => s.id === editingScopeFilters)?.name}</strong>. Modifică filtrele de mai sus, apoi salvează.</span>
            <div className="ml-auto flex items-center gap-2 shrink-0">
              <button
                onClick={async () => {
                  const filters: any = {};
                  if (filterRuling.length > 0) filters.ruling = filterRuling.join(',');
                  if (filterType) filters.tip_contestatie = filterType;
                  if (filterYears.length > 0) filters.years = filterYears.map(Number);
                  if (filterCritici.length > 0) filters.coduri_critici = filterCritici;
                  if (filterCpv.length > 0) filters.cpv_codes = filterCpv;
                  if (filterCategorie) filters.categorie = filterCategorie;
                  if (filterClasa) filters.clasa = filterClasa;
                  if (fileSearch.trim()) filters.search = fileSearch.trim();
                  if (filterMotivRespingere.length > 0) filters.motiv_respingere = filterMotivRespingere;
                  if (filterComplet.length > 0) filters.complet = filterComplet;
                  if (filterDomeniu.length > 0) filters.domeniu_legislativ = filterDomeniu;
                  if (filterTipProcedura.length > 0) filters.tip_procedura = filterTipProcedura;
                  if (filterCriteriuAtribuire.length > 0) filters.criteriu_atribuire = filterCriteriuAtribuire;
                  if (filterDateFrom) filters.data_decizie_from = filterDateFrom;
                  if (filterDateTo) filters.data_decizie_to = filterDateTo;
                  if (filterValoareMin) filters.valoare_min = filterValoareMin;
                  if (filterValoareMax) filters.valoare_max = filterValoareMax;
                  const scope = scopes.find(s => s.id === editingScopeFilters);
                  if (scope) {
                    await updateScope(scope.id, scope.name, scope.description, filters);
                  }
                  setEditingScopeFilters(null);
                }}
                className="bg-amber-600 text-white px-3 py-1.5 rounded-lg hover:bg-amber-700 font-medium flex items-center gap-1"
              >
                <Save size={12} /> Salvează filtrele
              </button>
              <button onClick={() => setEditingScopeFilters(null)} className="text-amber-500 hover:text-amber-700">
                <X size={14} />
              </button>
            </div>
          </div>
        )}

        {/* Decision Cards */}
        <div className="flex-1 overflow-y-auto px-4 md:px-6 py-4">
          {isLoadingDecisions ? (
            <div className="flex flex-col items-center justify-center py-20">
              <Loader2 size={32} className="text-blue-500 animate-spin mb-3" />
              <span className="text-sm text-slate-500">Se încarcă deciziile...</span>
            </div>
          ) : (
          <div className="space-y-3">
            {apiDecisions.map((dec: any) => {
              const snippet = dec.argumentatie_cnsc_snippet || dec.rezumat || '';
              const firstCritiqueCode = dec.coduri_critici?.[0];
              const critiqueDesc = firstCritiqueCode ? CRITIQUE_LEGEND[firstCritiqueCode] || '' : '';

              // Semaphore: green=all done, yellow=analyzed but no embeddings, grey=just imported
              const semColor = dec.has_embeddings ? 'bg-emerald-500' : dec.has_analysis ? 'bg-amber-400' : 'bg-slate-300';
              const semTooltip = [
                `Importat: Da`,
                `Analizat: ${dec.has_analysis ? 'Da' : 'Nu'}`,
                `Embedded: ${dec.has_embeddings ? 'Da' : 'Nu'}`,
              ].join('\n');

              return (
                <div key={dec.id}
                  className="group bg-white rounded-xl border border-slate-200/80 hover:border-blue-300 hover:shadow-md transition-all cursor-pointer flex items-stretch"
                  onClick={() => openDecision(`BO${dec.an_bo}_${dec.numar_bo}`)}
                >
                  {/* Main content area */}
                  <div className="flex-1 p-4 min-w-0">
                    {/* Row 1: ID + Semaphore */}
                    <div className="flex items-center gap-2.5 mb-2">
                      <span className="text-sm font-bold text-slate-900 font-mono tracking-tight">
                        BO{dec.an_bo}_{dec.numar_bo}
                      </span>
                      {/* Semaphore indicator */}
                      <div className="relative group/sem">
                        <div className={`w-2.5 h-2.5 rounded-full ${semColor} ring-2 ring-white shadow-sm`}></div>
                        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover/sem:block z-50">
                          <div className="bg-slate-800 text-white text-[10px] rounded-lg px-3 py-2 whitespace-pre shadow-lg leading-relaxed">
                            {semTooltip}
                          </div>
                          <div className="w-2 h-2 bg-slate-800 rotate-45 absolute left-1/2 -translate-x-1/2 -bottom-1"></div>
                        </div>
                      </div>
                    </div>

                    {/* Row 2: CPV */}
                    <p className="text-xs text-slate-500 mb-1.5 truncate">
                      <span className="font-semibold text-slate-600">{dec.cod_cpv || 'N/A'}</span>
                      {dec.cpv_descriere && <span className="text-slate-400"> — {dec.cpv_descriere}</span>}
                    </p>

                    {/* Row 3: Argumentație CNSC snippet */}
                    {snippet && (
                      <p className="text-xs text-slate-400 leading-relaxed line-clamp-2 mb-3">
                        {snippet}
                      </p>
                    )}

                    {/* Row 4: Tag Footer — Type + Ruling + Date + Critique codes + descriptions */}
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* Type pill */}
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                        dec.tip_contestatie === 'documentatie'
                          ? 'bg-purple-50 text-purple-600 border border-purple-200'
                          : 'bg-orange-50 text-orange-600 border border-orange-200'
                      }`}>
                        {dec.tip_contestatie === 'documentatie' ? 'Documentație' : 'Rezultat'}
                      </span>
                      {/* Ruling badge (moved from row 1) */}
                      {rulingBadge(dec.solutie_contestatie)}
                      {/* Date (moved from row 1) */}
                      {dec.data_decizie && (
                        <span className="text-[10px] text-slate-400 font-medium">
                          {new Date(dec.data_decizie).toLocaleDateString('ro-RO')}
                        </span>
                      )}
                      {/* Critique code pills + description */}
                      {dec.coduri_critici?.map((cod: string) => (
                        <span key={cod} className="text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full font-mono font-semibold border border-slate-200">
                          {cod}
                        </span>
                      ))}
                      {critiqueDesc && (
                        <span className="text-[10px] text-slate-400 truncate max-w-[200px] sm:max-w-[400px]">
                          {critiqueDesc}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Right actions */}
                  <div className="flex items-center border-l border-slate-100 group-hover:border-blue-100 transition-colors shrink-0">
                    <div
                      className="flex items-center justify-center px-3 py-3 hover:bg-blue-50 cursor-pointer transition-colors"
                      onClick={(e) => {
                        e.stopPropagation();
                        openDecision(`BO${dec.an_bo}_${dec.numar_bo}`, 'analysis');
                      }}
                      title="Vezi analiza LLM"
                    >
                      <Eye size={18} className="text-slate-300 group-hover:text-blue-500 transition-colors" />
                    </div>
                    {/* Admin actions: edit + delete */}
                    {authState.user?.rol === 'admin' && (
                      <>
                        <div
                          className="flex items-center justify-center px-2 py-3 hover:bg-amber-50 cursor-pointer transition-colors"
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingDecision(dec);
                            setEditDecisionForm({
                              solutie_contestatie: dec.solutie_contestatie || '',
                              tip_contestatie: dec.tip_contestatie || '',
                              contestator: dec.contestator || '',
                              autoritate_contractanta: dec.autoritate_contractanta || '',
                              cod_cpv: dec.cod_cpv || '',
                              cpv_descriere: dec.cpv_descriere || '',
                            });
                          }}
                          title="Editează decizia"
                        >
                          <Pencil size={14} className="text-slate-300 hover:text-amber-500 transition-colors" />
                        </div>
                        <div
                          className="flex items-center justify-center px-2 py-3 hover:bg-red-50 cursor-pointer transition-colors"
                          onClick={async (e) => {
                            e.stopPropagation();
                            const externalId = `BO${dec.an_bo}_${dec.numar_bo}`;
                            if (!confirm(`Sigur vrei să ștergi decizia ${externalId}?\nAceastă acțiune va șterge și analiza LLM asociată.`)) return;
                            try {
                              const res = await authFetch(`/api/v1/decisions/${externalId}`, { method: 'DELETE' });
                              if (res.ok) {
                                fetchDecisions(apiDecisionsPage, fileSearch);
                                authFetch('/api/v1/decisions/stats/overview').then(r => r.ok ? r.json() : null).then(d => d && setDbStats(d)).catch(() => {});
                              } else {
                                const data = await res.json().catch(() => ({}));
                                alert(`Eroare la ștergere: ${data.detail || res.status}`);
                              }
                            } catch (err: any) {
                              alert(`Eroare: ${err.message}`);
                            }
                          }}
                          title="Șterge decizia"
                        >
                          <Trash2 size={14} className="text-slate-300 hover:text-red-500 transition-colors" />
                        </div>
                      </>
                    )}
                  </div>
                </div>
              );
            })}

            {apiDecisions.length === 0 && (
              <div className="text-center py-20 text-slate-400 flex flex-col items-center">
                <div className="w-20 h-20 bg-slate-100 rounded-full flex items-center justify-center mb-4">
                  <Database size={32} className="text-slate-300" />
                </div>
                <h3 className="text-lg font-medium text-slate-600 mb-1">
                  {totalDecisions === 0 ? 'Baza de date este goală' : 'Nu s-au găsit rezultate'}
                </h3>
                <p className="max-w-md mx-auto text-sm">
                  {totalDecisions === 0
                    ? 'Nu există decizii CNSC în baza de date. Importă decizii pentru a începe.'
                    : 'Încearcă o altă căutare sau modifică filtrele.'}
                </p>
              </div>
            )}
          </div>
          )}
        </div>

        {/* Pagination */}
        <div className="bg-white px-4 md:px-6 py-3 border-t border-slate-200 text-xs text-slate-500 flex flex-col sm:flex-row justify-between items-center gap-2 shrink-0">
          <span className="font-medium">Pagina {apiDecisionsPage} din {totalPages || 1} <span className="text-slate-400">({apiDecisionsTotal.toLocaleString()} decizii)</span></span>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => goToPage(apiDecisionsPage - 1)}
              disabled={apiDecisionsPage <= 1}
              className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed text-xs font-medium transition"
            >
              Anterior
            </button>
            {totalPages > 0 && Array.from({length: Math.min(5, totalPages)}, (_, i) => {
              let page: number;
              if (totalPages <= 5) {
                page = i + 1;
              } else if (apiDecisionsPage <= 3) {
                page = i + 1;
              } else if (apiDecisionsPage >= totalPages - 2) {
                page = totalPages - 4 + i;
              } else {
                page = apiDecisionsPage - 2 + i;
              }
              return (
                <button
                  key={page}
                  onClick={() => goToPage(page)}
                  className={`w-8 h-8 rounded-lg text-xs font-medium transition ${
                    page === apiDecisionsPage
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'border border-slate-200 hover:bg-slate-50 text-slate-600'
                  }`}
                >
                  {page}
                </button>
              );
            })}
            <button
              onClick={() => goToPage(apiDecisionsPage + 1)}
              disabled={apiDecisionsPage >= totalPages}
              className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed text-xs font-medium transition"
            >
              Următor
            </button>
          </div>
          <div className="hidden sm:flex items-center gap-1.5 text-emerald-600 font-medium">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500"></div>
            Connected
          </div>
        </div>
      </div>
    );
  };

  const renderDrafter = () => (
    <div className="h-full flex flex-col md:flex-row bg-white">
      <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-bold text-slate-800 flex gap-2 items-center">
            <Scale className="text-blue-600" size={20}/>
            {drafterDocType === 'contestatie' ? 'Configurare Contestație' : 'Configurare Plângere'}
          </h2>
          <button onClick={() => loadHistory('contestatie')} className="text-xs bg-slate-50 text-slate-500 px-2.5 py-1 rounded-lg font-medium hover:bg-slate-100 transition flex items-center gap-1" title="Istoric"><Bookmark size={12} /> Istoric</button>
        </div>

        {renderActiveDosarBanner((docs) => {
          setUploadedDocsDrafter(prev => [...prev, ...docs]);
        })}

        {/* Document Type Selector */}
        <div className="flex gap-2 mb-4 mt-2">
          <button
            onClick={() => setDrafterDocType('contestatie')}
            className={`flex-1 text-xs font-semibold py-2.5 rounded-lg border transition ${drafterDocType === 'contestatie' ? 'bg-blue-600 text-white border-blue-600 shadow-sm' : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'}`}
          >
            Contestație CNSC
          </button>
          <button
            onClick={() => setDrafterDocType('plangere')}
            className={`flex-1 text-xs font-semibold py-2.5 rounded-lg border transition ${drafterDocType === 'plangere' ? 'bg-purple-600 text-white border-purple-600 shadow-sm' : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'}`}
          >
            Plângere instanță
          </button>
        </div>
        <p className="text-[10px] text-slate-400 mb-4">
          {drafterDocType === 'contestatie'
            ? 'Generează o contestație către CNSC împotriva actelor autorității contractante.'
            : 'Generează o plângere la Curtea de Apel împotriva unei decizii CNSC.'}
        </p>

        <ScopeSelector compact />
        <ActiveScopeIndicator />

        <div className="space-y-5">
          <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
            <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
              Încarcă document (.txt, .md, .pdf)
            </label>
            <input
              type="file"
              accept=".txt,.md,.pdf,.doc,.docx"
              onChange={(e) => handleDocumentUpload(e, (text) => setDrafterContext(prev => ({...prev, facts: prev.facts ? prev.facts + '\n\n---\n\n' + text : text})), (doc) => setUploadedDocsDrafter(prev => [...prev, doc]))}
              className="block w-full text-sm text-slate-600
                file:mr-4 file:py-1.5 file:px-3
                file:rounded-lg file:border-0
                file:text-xs file:font-semibold
                file:bg-blue-50 file:text-blue-700
                hover:file:bg-blue-100"
            />
            {uploadedDocsDrafter.length > 0 && (
              <div className="mt-2 space-y-1">
                {uploadedDocsDrafter.map((doc, idx) => (
                  <div key={idx} className="flex items-center justify-between text-xs text-green-600 bg-green-50 rounded px-2 py-1">
                    <span>✓ {doc.name} ({doc.text.length.toLocaleString()} car.)</span>
                    <button onClick={() => setUploadedDocsDrafter(prev => prev.filter((_, i) => i !== idx))} className="text-red-400 hover:text-red-600 ml-2" title="Șterge">✕</button>
                  </div>
                ))}
                {uploadedDocsDrafter.length > 1 && (
                  <button onClick={() => { setUploadedDocsDrafter([]); setDrafterContext(prev => ({...prev, facts: ''})); }} className="text-xs text-red-500 hover:text-red-700 underline">Șterge toate</button>
                )}
              </div>
            )}
          </div>

          {/* Plângere-specific: CNSC decision number */}
          {drafterDocType === 'plangere' && (
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Nr. Decizie CNSC atacată</label>
              <input
                type="text"
                className="w-full p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 outline-none transition shadow-sm"
                placeholder="Ex: Decizia nr. 1234/C8/5678 din 10.01.2026"
                value={drafterContext.numarDecizieCnsc}
                onChange={(e) => setDrafterContext({...drafterContext, numarDecizieCnsc: e.target.value})}
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
              {drafterDocType === 'contestatie' ? 'Situația de Fapt' : 'Motivele Plângerii'}
            </label>
            <textarea
              className={`w-full p-3 border rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.facts.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder={drafterDocType === 'contestatie' ? 'Descrie cronologia evenimentelor sau încarcă un document...' : 'Descrie motivele pentru care decizia CNSC este nelegală/netemeinică...'}
              value={drafterContext.facts}
              onChange={(e) => setDrafterContext({...drafterContext, facts: e.target.value})}
            />
            <CharCounter value={drafterContext.facts} maxLength={200000} />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Argumentele Autorității / CNSC</label>
            <textarea
              className={`w-full p-3 border rounded-lg text-sm h-24 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.authorityArgs.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder={drafterDocType === 'contestatie' ? 'Ce motive a invocat autoritatea pentru respingere?' : 'Ce a reținut CNSC în decizia atacată?'}
              value={drafterContext.authorityArgs}
              onChange={(e) => setDrafterContext({...drafterContext, authorityArgs: e.target.value})}
            />
            <CharCounter value={drafterContext.authorityArgs} maxLength={200000} />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Temei Legal</label>
            <input
              type="text"
              className={`w-full p-3 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.legalGrounds.length > 50000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder="Ex: Art. 215 Legea 98/2016"
              value={drafterContext.legalGrounds}
              onChange={(e) => setDrafterContext({...drafterContext, legalGrounds: e.target.value})}
            />
            <CharCounter value={drafterContext.legalGrounds} maxLength={50000} />
          </div>

          {/* Collapsible advanced fields */}
          <details className="group">
            <summary className="text-xs font-semibold text-slate-500 uppercase cursor-pointer hover:text-blue-600 transition flex items-center gap-1">
              <ChevronRight size={12} className="group-open:rotate-90 transition-transform" />
              Câmpuri avansate (opțional)
            </summary>
            <div className="space-y-4 mt-3 pl-1">
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Detalii Procedură</label>
                <textarea
                  className="w-full p-3 border border-slate-300 rounded-lg text-sm h-20 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm"
                  placeholder="Tip procedură, valoare estimată, criteriu atribuire, nr. oferte..."
                  value={drafterContext.detaliiProcedura}
                  onChange={(e) => setDrafterContext({...drafterContext, detaliiProcedura: e.target.value})}
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Remedii Solicitate</label>
                <textarea
                  className="w-full p-3 border border-slate-300 rounded-lg text-sm h-20 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm"
                  placeholder="Ex: Anularea actului, reevaluarea ofertelor, refacerea documentației..."
                  value={drafterContext.remediiSolicitate}
                  onChange={(e) => setDrafterContext({...drafterContext, remediiSolicitate: e.target.value})}
                />
              </div>
            </div>
          </details>

          <button
            onClick={handleDrafting}
            disabled={isLoading}
            className={`w-full text-white py-4 rounded-xl font-medium transition flex justify-center items-center gap-2 shadow-lg hover:shadow-xl mt-4 ${
              drafterDocType === 'plangere' ? 'bg-purple-700 hover:bg-purple-600' : 'bg-slate-900 hover:bg-slate-800'
            }`}
          >
            {isLoading ? <><Loader2 className="animate-spin" size={18} /> <span className="text-sm">{streamStatus || "Se procesează..."}</span></> : drafterDocType === 'contestatie' ? 'Generează Contestație' : 'Generează Plângere'}
          </button>
        </div>
      </div>
      
      <div className="w-full md:w-2/3 p-4 md:p-10 overflow-y-auto bg-white">
        {generatedContent ? (
          <div className="max-w-3xl mx-auto">
             <div className="flex justify-end gap-3 mb-4">
                <button className="text-sm text-blue-600 font-medium hover:underline">Descarcă .DOCX</button>
                <button onClick={() => saveDocument(drafterDocType === 'plangere' ? 'plangere' : 'contestatie', drafterContext.facts.slice(0, 200) || (drafterDocType === 'plangere' ? 'Plângere' : 'Contestație'), generatedContent, generatedDecisionRefs, { facts: drafterContext.facts, authorityArgs: drafterContext.authorityArgs, legalGrounds: drafterContext.legalGrounds, docType: drafterDocType })} className="text-sm text-green-600 font-medium hover:underline flex items-center gap-1"><Save size={12} /> Salvează</button>
                <button onClick={() => loadHistory('contestatie')} className="text-sm text-slate-500 font-medium hover:underline flex items-center gap-1"><Bookmark size={12} /> Istoric</button>
             </div>
             <div className="prose prose-slate max-w-none font-serif text-slate-800 leading-loose bg-white" dangerouslySetInnerHTML={{ __html: formatMarkdown(generatedContent) }} />
             {generatedDecisionRefs.length > 0 && (
               <div className="mt-6 p-4 bg-blue-50 border border-blue-200 rounded-xl">
                 <p className="font-semibold text-slate-700 mb-2 text-sm">📚 Jurisprudență CNSC utilizată:</p>
                 <div className="flex flex-wrap gap-2">
                   {generatedDecisionRefs.map((ref: string) => (
                     <span key={ref} className="text-xs bg-white text-blue-700 px-3 py-1.5 rounded-lg border border-blue-200 font-mono cursor-pointer hover:bg-blue-100 transition" onClick={() => openDecision(ref)}>{ref}</span>
                   ))}
                 </div>
               </div>
             )}
          </div>
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-slate-300">
            <Scale size={64} className="mb-6 opacity-20" />
            <p className="text-lg font-medium">Configurează parametrii contestației</p>
            <p className="text-sm mt-2">AI-ul va genera structura juridică completă.</p>
          </div>
        )}
      </div>
    </div>
  );

  const trainingMaterialTypes: Record<string, { name: string; desc: string }> = {
    speta: { name: "Speță practică", desc: "Scenariu realist cu analiză juridică" },
    studiu_caz: { name: "Studiu de caz", desc: "Analiză aprofundată, multiple perspective" },
    situational: { name: "Întrebări situaționale", desc: "Scenarii decizionale 'Ce ați face dacă...'" },
    palarii: { name: "Pălăriile Gânditoare", desc: "6 perspective (de Bono)" },
    dezbatere: { name: "Dezbatere Pro & Contra", desc: "Argumente pro/contra cu temei legal" },
    quiz: { name: "Quiz cu variante", desc: "Întrebări cu răspunsuri multiple A/B/C/D" },
    joc_rol: { name: "Joc de rol", desc: "Scenarii cu roluri și instrucțiuni" },
    erori: { name: "Identificare erori", desc: "Document cu greșeli de identificat" },
    comparativ: { name: "Analiză comparativă", desc: "Compararea a două abordări" },
    cronologie: { name: "Cronologie procedurală", desc: "Ordonarea pașilor unei proceduri" },
  };

  const buildTrainingRequestBody = (tipOverride?: string) => ({
    tema: trainingTema,
    tip_material: tipOverride || trainingTip,
    nivel_dificultate: trainingNivel,
    lungime: trainingLungime,
    context_suplimentar: trainingContext,
    public_tinta: trainingPublicTinta || undefined,
    program_plan: trainingMode === 'program' ? trainingProgramPlan : undefined,
    scope_id: activeScopeId || undefined,
  });

  // Toggle a material type in multi-select
  const toggleTrainingType = (key: string) => {
    setTrainingSelectedTypes(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    );
  };

  const handleTrainingGenerate = async () => {
    if (!trainingTema.trim() || trainingLoading) return;

    const resetUI = () => {
      setTrainingLoading(true);
      setTrainingResult("");
      setTrainingMeta(null);
      setTrainingActiveTab('material');
      setTrainingEditing(false);
      setTrainingEditedResult(null);
    };

    // Program mode — LLM generates the full program
    if (trainingMode === 'program') {
      resetUI();

      await authFetchStream(
        '/api/v1/training/generate/stream',
        {
          ...buildTrainingRequestBody('program_formare'),
          selected_types: trainingSelectedTypes.length > 0 ? trainingSelectedTypes : undefined,
        },
        (text) => setTrainingResult(prev => prev + text),
        (meta) => { setTrainingMeta(meta); setTrainingLoading(false); refreshAuthUser(); },
        (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); setTrainingLoading(false); refreshAuthUser(); },
      );
      return;
    }

    // Batch mode — generate multiple materials sequentially, streaming each into view
    if (trainingMode === 'batch' && trainingBatchCount > 1) {
      resetUI();
      setTrainingBatchProgress({ current: 0, total: trainingBatchCount, results: [] });

      // Determine type sequence: cycle through selected types
      const typesToUse = trainingSelectedTypes.length > 0 ? trainingSelectedTypes : [trainingTip];

      for (let i = 0; i < trainingBatchCount; i++) {
        const tipForThis = typesToUse[i % typesToUse.length];
        const tipName = trainingMaterialTypes[tipForThis]?.name || tipForThis;
        setTrainingBatchProgress(prev => prev ? { ...prev, current: i + 1 } : null);

        // Add header for this material before streaming begins
        const header = `\n\n---\n\n# Material ${i + 1} din ${trainingBatchCount} — ${tipName}\n\n`;
        setTrainingResult(prev => prev + header);

        await authFetchStream(
          '/api/v1/training/generate/stream',
          {
            ...buildTrainingRequestBody(tipForThis),
            batch_index: i + 1,
            batch_total: trainingBatchCount,
          },
          // Stream each chunk directly into the visible result
          (text) => setTrainingResult(prev => prev + text),
          () => {},
          (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); },
        );
      }

      setTrainingBatchProgress(null);
      setTrainingLoading(false);
      refreshAuthUser();
      return;
    }

    // Individual mode (default)
    resetUI();

    await authFetchStream(
      '/api/v1/training/generate/stream',
      buildTrainingRequestBody(),
      (text) => setTrainingResult(prev => prev + text),
      (meta) => { setTrainingMeta(meta); setTrainingLoading(false); refreshAuthUser(); },
      (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); setTrainingLoading(false); refreshAuthUser(); },
    );
  };

  const handleTrainingExport = async (format: 'docx' | 'pdf' | 'md') => {
    const contentToExport = trainingEditedResult ?? trainingResult;
    if (!contentToExport) return;
    try {
      const response = await authFetch('/api/v1/training/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: contentToExport,
          format,
          titlu: `TrainingAP - ${trainingMaterialTypes[trainingTip]?.name || trainingTip}`,
          metadata: trainingMeta,
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ext = format === 'docx' ? 'docx' : format === 'pdf' ? 'pdf' : 'md';
      a.download = `TrainingAP_${trainingTip}_${trainingNivel}.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
    }
  };

  const parseTrainingSections = (text: string) => {
    const sections: Record<string, string> = {};
    let currentKey: string | null = null;
    let currentLines: string[] = [];

    for (const line of text.split('\n')) {
      const lower = line.trim().toLowerCase();
      if (lower.startsWith('## enun') || lower.startsWith('## cerin')) {
        if (currentKey) sections[currentKey] = currentLines.join('\n').trim();
        currentKey = lower.startsWith('## enun') ? 'material' : 'material';
        if (lower.startsWith('## cerin')) currentKey = 'material';
        // Keep Enunț and Cerințe together in "material" tab
        currentLines.push(line);
        continue;
      } else if (lower.startsWith('## rezolv')) {
        if (currentKey) sections[currentKey] = currentLines.join('\n').trim();
        currentKey = 'rezolvare';
        currentLines = [];
        continue;
      } else if (lower.startsWith('## note')) {
        if (currentKey) sections[currentKey] = currentLines.join('\n').trim();
        currentKey = 'note';
        currentLines = [];
        continue;
      }
      currentLines.push(line);
    }
    if (currentKey) sections[currentKey] = currentLines.join('\n').trim();

    // If no sections parsed, put everything in material
    if (!sections.material && !sections.rezolvare && !sections.note) {
      sections.material = text;
    }

    return sections;
  };

  // ---------------------------------------------------------------------------
  // Analytics Page — Panel Profiles, Outcome Predictor, Decision Comparison
  // ---------------------------------------------------------------------------
  const loadPanelsList = async () => {
    setPanelsLoading(true);
    try {
      const res = await authFetch('/api/v1/analytics/panels');
      if (res.ok) setPanelsList(await res.json());
    } catch (e) { console.error(e); }
    setPanelsLoading(false);
  };
  const loadPanelProfile = async (complet: string) => {
    setSelectedPanel(complet);
    setPanelProfile(null);
    setPanelsLoading(true);
    try {
      const res = await authFetch(`/api/v1/analytics/panel/${complet}`);
      if (res.ok) setPanelProfile(await res.json());
    } catch (e) { console.error(e); }
    setPanelsLoading(false);
  };
  const loadAnalyticsFilters = async () => {
    try {
      const [critRes, compRes, procRes] = await Promise.all([
        authFetch('/api/v1/decisions/filters/critici-codes'),
        authFetch('/api/v1/decisions/filters/complete'),
        authFetch('/api/v1/decisions/filters/tipuri-procedura'),
      ]);
      const critici = critRes.ok ? await critRes.json() : [];
      const complete = compRes.ok ? await compRes.json() : [];
      const proceduri = procRes.ok ? await procRes.json() : [];
      setAnalyticsFilterOptions({
        critici: critici.filter((c: any) => (CRITIQUE_LEGEND as any)[c.code]),
        complete: complete.filter((c: any) => /^C\d{1,2}$/.test(c.name)).sort((a: any, b: any) => parseInt(a.name.slice(1)) - parseInt(b.name.slice(1))),
        proceduri,
      });
    } catch (e) { console.error(e); }
  };
  const runPrediction = async () => {
    const codes = predictorInput.coduri_critici;
    if (codes.length === 0) return;
    setPredictorLoading(true);
    setPredictorResult(null);
    try {
      const body: any = { coduri_critici: codes };
      if (predictorInput.cod_cpv) body.cod_cpv = predictorInput.cod_cpv;
      if (predictorInput.complet) body.complet = predictorInput.complet;
      if (predictorInput.tip_procedura) body.tip_procedura = predictorInput.tip_procedura;
      if (predictorInput.tip_contestatie) body.tip_contestatie = predictorInput.tip_contestatie;
      const res = await authFetch('/api/v1/analytics/predict-outcome', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (res.ok) setPredictorResult(await res.json());
    } catch (e) { console.error(e); }
    setPredictorLoading(false);
  };
  const runComparison = async () => {
    const ids = compareIds.filter(Boolean);
    if (ids.length < 2) return;
    setCompareLoading(true);
    setCompareResult(null);
    try {
      const res = await authFetch('/api/v1/analytics/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision_ids: ids }) });
      if (res.ok) setCompareResult(await res.json());
    } catch (e) { console.error(e); }
    setCompareLoading(false);
  };

  // =========================================================================
  // MULTI-DOCUMENT ANALYSIS PAGE
  // =========================================================================
  const runMultiDocAnalysis = async () => {
    if (multiDocFiles.length < 2) return;
    setMultiDocLoading(true);
    setMultiDocResult(null);
    try {
      const formData = new FormData();
      multiDocFiles.forEach(f => formData.append('files', f));
      formData.append('use_jurisprudence', 'true');
      const res = await authFetch('/api/v1/multi-document/analyze', { method: 'POST', body: formData });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Eroare ${res.status}`);
      setMultiDocResult(await res.json());
    } catch (err: any) {
      alert(`Eroare: ${err.message}`);
    } finally {
      setMultiDocLoading(false);
    }
  };

  const renderMultiDocument = () => (
    <div className="h-full flex flex-col md:flex-row bg-white">
      <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
        <h2 className="text-lg font-bold text-slate-800 mb-2 flex gap-2 items-center">
          <Files className="text-violet-600" size={20}/> Analiză Multi-Document
        </h2>
        <p className="text-xs text-slate-500 mb-6">Încarcă 2-5 documente din dosarul de achiziție pentru analiză unificată: red flags per document + inconsistențe între documente.</p>

        <div className="space-y-4">
          <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-violet-300">
            <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">Documente dosar (2-5 fișiere)</label>
            <input type="file" multiple accept=".pdf,.docx,.doc,.txt,.md"
              onChange={(e) => setMultiDocFiles(Array.from(e.target.files || []))}
              className="w-full text-xs text-slate-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-violet-50 file:text-violet-600 hover:file:bg-violet-100" />
          </div>
          {multiDocFiles.length > 0 && (
            <div className="bg-white border border-slate-200 rounded-lg p-3">
              <p className="text-xs font-bold text-slate-500 uppercase mb-2">{multiDocFiles.length} fișiere selectate</p>
              {multiDocFiles.map((f, i) => (
                <div key={i} className="flex items-center justify-between py-1 text-xs text-slate-600">
                  <span className="truncate flex-1">{f.name}</span>
                  <span className="text-slate-400 ml-2">{(f.size / 1024).toFixed(0)} KB</span>
                </div>
              ))}
            </div>
          )}
          <button onClick={runMultiDocAnalysis}
            disabled={multiDocLoading || multiDocFiles.length < 2}
            className="w-full bg-violet-600 text-white py-3 rounded-xl font-semibold hover:bg-violet-700 disabled:opacity-50 transition flex items-center justify-center gap-2 text-sm">
            {multiDocLoading ? <><Loader2 className="w-4 h-4 animate-spin" /> Se analizează dosarul...</> : <><Files size={16} /> Analizează Dosarul</>}
          </button>
          {multiDocLoading && (
            <p className="text-xs text-slate-400 text-center">Analiza poate dura 2-5 minute pentru documente mari.</p>
          )}
        </div>
      </div>

      <div className="w-full md:w-2/3 p-4 md:p-8 overflow-y-auto bg-white">
        {!multiDocResult && !multiDocLoading && (
          <div className="h-full flex items-center justify-center text-slate-400">
            <div className="text-center">
              <Files size={48} className="mx-auto mb-4 opacity-30" />
              <p className="text-lg font-medium">Analiză Multi-Document</p>
              <p className="text-sm mt-1">Încarcă minim 2 documente pentru analiză</p>
            </div>
          </div>
        )}
        {multiDocLoading && (
          <div className="h-full flex items-center justify-center">
            <div className="text-center">
              <Loader2 size={40} className="animate-spin text-violet-500 mx-auto mb-4" />
              <p className="text-slate-600 font-medium">Se analizează {multiDocFiles.length} documente...</p>
            </div>
          </div>
        )}
        {multiDocResult && (
          <div className="max-w-3xl mx-auto space-y-6">
            {/* Unified assessment */}
            <div className={`rounded-2xl p-6 ${multiDocResult.unified_assessment?.risk_level === 'LOW' ? 'bg-green-50 border-2 border-green-300' : multiDocResult.unified_assessment?.risk_level === 'CRITICAL' ? 'bg-red-50 border-2 border-red-300' : 'bg-amber-50 border-2 border-amber-300'}`}>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-lg font-bold text-slate-800">Evaluare Unificată</h3>
                <span className={`px-3 py-1 rounded-full text-sm font-bold ${multiDocResult.unified_assessment?.risk_level === 'LOW' ? 'bg-green-200 text-green-800' : multiDocResult.unified_assessment?.risk_level === 'CRITICAL' ? 'bg-red-200 text-red-800' : 'bg-amber-200 text-amber-800'}`}>
                  {multiDocResult.unified_assessment?.risk_level}
                </span>
              </div>
              <p className="text-sm text-slate-700">{multiDocResult.unified_assessment?.text}</p>
              <div className="flex gap-6 mt-3 text-xs text-slate-500">
                <span>{multiDocResult.total_flags} red flags total</span>
                <span>{multiDocResult.critical_flags} critice</span>
                <span>{multiDocResult.cross_issues_count} inconsistențe</span>
              </div>
            </div>

            {/* Cross-document issues */}
            {multiDocResult.cross_document_issues?.length > 0 && (
              <div>
                <h3 className="text-lg font-bold text-slate-800 mb-3">Inconsistențe între Documente</h3>
                <div className="space-y-3">
                  {multiDocResult.cross_document_issues.map((issue: any, i: number) => (
                    <div key={i} className={`border rounded-xl p-4 ${issue.severitate === 'CRITICAL' ? 'border-red-200 bg-red-50/50' : issue.severitate === 'MEDIUM' ? 'border-amber-200 bg-amber-50/50' : 'border-slate-200'}`}>
                      <div className="flex items-center gap-2 mb-2">
                        <span className={`px-2 py-0.5 rounded text-xs font-bold ${issue.severitate === 'CRITICAL' ? 'bg-red-200 text-red-800' : 'bg-amber-200 text-amber-800'}`}>{issue.severitate}</span>
                        <span className="text-xs text-slate-500">{issue.tip}</span>
                      </div>
                      <p className="text-sm text-slate-700 mb-1">{issue.descriere}</p>
                      {issue.documente_implicate?.length > 0 && (
                        <p className="text-xs text-slate-400">Documente: {issue.documente_implicate.join(', ')}</p>
                      )}
                      {issue.recomandare && (
                        <p className="text-xs text-amber-700 mt-1 font-medium">{issue.recomandare}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Per-document results */}
            <div>
              <h3 className="text-lg font-bold text-slate-800 mb-3">Red Flags per Document</h3>
              <div className="space-y-4">
                {(multiDocResult.per_document || []).map((doc: any, i: number) => (
                  <div key={i} className="border border-slate-200 rounded-xl overflow-hidden">
                    <div className="bg-slate-50 px-4 py-3 flex items-center justify-between">
                      <span className="font-medium text-slate-800 text-sm">{doc.filename}</span>
                      <div className="flex items-center gap-3 text-xs">
                        <span className="text-slate-400">{doc.word_count} cuvinte</span>
                        <span className={`font-bold ${doc.flag_count > 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {doc.flag_count} flags
                        </span>
                      </div>
                    </div>
                    {doc.flags?.length > 0 && (
                      <div className="p-4 space-y-2">
                        {doc.flags.slice(0, 5).map((flag: any, j: number) => (
                          <div key={j} className="text-sm text-slate-700 flex items-start gap-2">
                            <span className={`mt-0.5 w-2 h-2 rounded-full flex-shrink-0 ${flag.severity === 'CRITICAL' ? 'bg-red-500' : flag.severity === 'MEDIUM' ? 'bg-amber-500' : 'bg-slate-400'}`} />
                            <span>{flag.issue || flag.clause?.slice(0, 200)}</span>
                          </div>
                        ))}
                        {doc.flags.length > 5 && (
                          <p className="text-xs text-slate-400">... și încă {doc.flags.length - 5} alte probleme</p>
                        )}
                      </div>
                    )}
                    {doc.error && (
                      <p className="px-4 py-2 text-xs text-red-600">{doc.error}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  // =========================================================================
  // COMPLIANCE PAGE
  // =========================================================================
  const runComplianceCheck = async () => {
    if (!complianceText.trim() && !complianceFile) return;
    setComplianceLoading(true);
    setComplianceResult(null);
    try {
      const formData = new FormData();
      if (complianceFile) {
        formData.append('file', complianceFile);
      } else {
        formData.append('text', complianceText);
      }
      if (complianceProcedura) formData.append('tip_procedura', complianceProcedura);
      formData.append('tip_document', 'documentație achiziție');

      const res = await authFetch('/api/v1/compliance/check', { method: 'POST', body: formData });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Eroare ${res.status}`);
      setComplianceResult(await res.json());
    } catch (err: any) {
      alert(`Eroare: ${err.message}`);
    } finally {
      setComplianceLoading(false);
    }
  };

  const renderCompliance = () => {
    if (analyticsFilterOptions.proceduri.length === 0) loadAnalyticsFilters();
    return (
      <div className="h-full flex flex-col md:flex-row bg-white">
        {/* Left panel */}
        <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-slate-800 flex gap-2 items-center">
              <ClipboardCheck className="text-emerald-600" size={20}/> Verificator Conformitate
            </h2>
          </div>
          <p className="text-xs text-slate-500 mb-4">Încarcă documentația de achiziție și AI verifică conformitatea cu legislația aplicabilă.</p>

          {renderActiveDosarBanner((docs) => {
            const combined = docs.map((d, i) => `=== DOCUMENT ${i+1}: ${d.name} ===\n${d.text}`).join('\n\n---\n\n');
            setComplianceText(combined);
          })}

          <div className="space-y-4 mt-2">
            <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
              <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">Încarcă document (.pdf, .docx, .txt)</label>
              <input type="file" accept=".pdf,.docx,.doc,.txt,.md"
                onChange={(e) => { setComplianceFile(e.target.files?.[0] || null); setComplianceText(''); }}
                className="w-full text-xs text-slate-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-emerald-50 file:text-emerald-600 hover:file:bg-emerald-100" />
            </div>
            <div className="text-center text-xs text-slate-400">— sau —</div>
            <div>
              <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Textul documentului</label>
              <textarea rows={8} value={complianceText}
                onChange={e => setComplianceText(e.target.value)}
                placeholder="Lipește textul documentației de achiziție aici..."
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm resize-none focus:ring-2 focus:ring-emerald-500/40 outline-none" />
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Tip procedură</label>
              <select value={complianceProcedura}
                onChange={e => setComplianceProcedura(e.target.value)}
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white">
                <option value="">— Autodetect —</option>
                {analyticsFilterOptions.proceduri.map((p: any) => (
                  <option key={p.name} value={p.name}>{p.name}</option>
                ))}
              </select>
            </div>

            <button onClick={runComplianceCheck}
              disabled={complianceLoading || (!complianceText.trim() && !complianceFile)}
              className="w-full bg-emerald-600 text-white py-3 rounded-xl font-semibold hover:bg-emerald-700 disabled:opacity-50 transition flex items-center justify-center gap-2 text-sm">
              {complianceLoading ? <><Loader2 className="w-4 h-4 animate-spin" /> Se verifică conformitatea...</> : <><ClipboardCheck size={16} /> Verifică Conformitate</>}
            </button>
          </div>
        </div>

        {/* Right panel — results */}
        <div className="w-full md:w-2/3 p-4 md:p-8 overflow-y-auto bg-white">
          {!complianceResult && !complianceLoading && (
            <div className="h-full flex items-center justify-center text-slate-400">
              <div className="text-center">
                <ClipboardCheck size={48} className="mx-auto mb-4 opacity-30" />
                <p className="text-lg font-medium">Verificator Conformitate</p>
                <p className="text-sm mt-1">Încarcă un document pentru verificare</p>
              </div>
            </div>
          )}
          {complianceLoading && (
            <div className="h-full flex items-center justify-center">
              <div className="text-center">
                <Loader2 size={40} className="animate-spin text-emerald-500 mx-auto mb-4" />
                <p className="text-slate-600 font-medium">Se verifică conformitatea cu legislația...</p>
                <p className="text-xs text-slate-400 mt-1">Poate dura 30-90 secunde</p>
              </div>
            </div>
          )}
          {complianceResult && (
            <div className="max-w-3xl mx-auto space-y-6">
              {/* Score header */}
              <div className={`rounded-2xl p-6 text-center ${(complianceResult.score ?? 0) >= 80 ? 'bg-green-50 border-2 border-green-300' : (complianceResult.score ?? 0) >= 50 ? 'bg-amber-50 border-2 border-amber-300' : 'bg-red-50 border-2 border-red-300'}`}>
                <div className={`text-4xl font-black mb-2 ${(complianceResult.score ?? 0) >= 80 ? 'text-green-700' : (complianceResult.score ?? 0) >= 50 ? 'text-amber-700' : 'text-red-700'}`}>
                  {complianceResult.score}%
                </div>
                <p className="text-sm text-slate-600">Scor conformitate</p>
                <div className="flex justify-center gap-6 mt-3 text-sm">
                  <span className="text-green-700 font-semibold">{complianceResult.conform} conforme</span>
                  <span className="text-red-700 font-semibold">{complianceResult.neconform} neconforme</span>
                  <span className="text-slate-500">{complianceResult.neclar} neclar</span>
                </div>
              </div>

              {/* Summary */}
              {complianceResult.summary && (
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-4">
                  <div className="prose prose-sm max-w-none text-slate-700" dangerouslySetInnerHTML={{ __html: formatMarkdown(complianceResult.summary) }} />
                </div>
              )}

              {/* Compliance items */}
              <div>
                <h3 className="text-lg font-bold text-slate-800 mb-3">Matrice Conformitate</h3>
                <div className="space-y-3">
                  {(complianceResult.compliance_items || []).map((item: any, i: number) => (
                    <div key={i} className={`border rounded-xl p-4 ${item.verdict === 'CONFORM' ? 'border-green-200 bg-green-50/50' : item.verdict === 'NECONFORM' ? 'border-red-200 bg-red-50/50' : 'border-slate-200 bg-slate-50/50'}`}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-mono text-sm font-bold text-slate-800">{item.citare}</span>
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ${item.verdict === 'CONFORM' ? 'bg-green-200 text-green-800' : item.verdict === 'NECONFORM' ? 'bg-red-200 text-red-800' : 'bg-slate-200 text-slate-600'}`}>
                          {item.verdict}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 mb-1">{item.act}</p>
                      <p className="text-sm text-slate-700">{item.explicatie}</p>
                      {item.recomandare && (
                        <div className="mt-2 p-2 bg-amber-50 rounded-lg border border-amber-200">
                          <p className="text-xs font-bold text-amber-700">Recomandare:</p>
                          <p className="text-xs text-amber-800">{item.recomandare}</p>
                        </div>
                      )}
                      {item.citat_document && (
                        <div className="mt-2 text-xs text-slate-500 italic border-l-2 border-slate-300 pl-2">
                          &ldquo;{item.citat_document}&rdquo;
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  // =========================================================================
  // STRATEGY PAGE
  // =========================================================================
  const extractEntitiesFromFile = async (file: File, target: 'strategy' | 'compliance') => {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await authFetch('/api/v1/documents/extract-entities', { method: 'POST', body: formData });
      if (!res.ok) return;
      const entities = await res.json();
      if (target === 'strategy') {
        setStrategyInput(p => ({
          ...p,
          cod_cpv: entities.cod_cpv || p.cod_cpv,
          tip_procedura: entities.tip_procedura || p.tip_procedura,
          tip_contestatie: entities.tip_contract === 'lucrări' || entities.tip_contract === 'servicii' || entities.tip_contract === 'furnizare' ? p.tip_contestatie : p.tip_contestatie,
          valoare_estimata: entities.valoare_estimata ? String(entities.valoare_estimata) : p.valoare_estimata,
          description: entities.obiect_contract ? (p.description ? p.description + '\n\nObiect contract: ' + entities.obiect_contract : 'Obiect contract: ' + entities.obiect_contract) : p.description,
        }));
      }
    } catch { /* silent */ }
  };

  const runStrategy = async () => {
    if (strategyInput.coduri_critici.length === 0 || !strategyInput.description.trim()) return;
    setStrategyLoading(true);
    setStrategyResult(null);
    try {
      const body: any = {
        description: strategyInput.description,
        coduri_critici: strategyInput.coduri_critici,
      };
      if (strategyInput.cod_cpv) body.cod_cpv = strategyInput.cod_cpv;
      if (strategyInput.complet) body.complet = strategyInput.complet;
      if (strategyInput.tip_procedura) body.tip_procedura = strategyInput.tip_procedura;
      if (strategyInput.tip_contestatie) body.tip_contestatie = strategyInput.tip_contestatie;
      if (strategyInput.valoare_estimata) body.valoare_estimata = parseFloat(strategyInput.valoare_estimata);
      const res = await authFetch('/api/v1/strategy/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Eroare ${res.status}`);
      setStrategyResult(await res.json());
    } catch (err: any) {
      alert(`Eroare: ${err.message}`);
    } finally {
      setStrategyLoading(false);
    }
  };

  const renderStrategy = () => {
    if (analyticsFilterOptions.critici.length === 0) loadAnalyticsFilters();
    return (
      <div className="h-full flex flex-col md:flex-row bg-white">
        {/* Left panel — input */}
        <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-slate-800 flex gap-2 items-center">
              <Target className="text-indigo-600" size={20}/> Strategie Contestare
            </h2>
            <button onClick={() => loadHistory('contestatie')} className="text-xs bg-slate-50 text-slate-500 px-2.5 py-1 rounded-lg font-medium hover:bg-slate-100 transition flex items-center gap-1" title="Istoric"><Bookmark size={12} /> Istoric</button>
          </div>
          <p className="text-xs text-slate-500 mb-4">AI generează o strategie completă de contestare cu șanse de succes per critică, temei legal și jurisprudență.</p>

          {renderActiveDosarBanner((docs) => {
            const combined = docs.map((d, i) => `=== DOCUMENT ${i+1}: ${d.name} ===\n${d.text}`).join('\n\n---\n\n');
            setStrategyInput(p => ({ ...p, description: p.description ? p.description + '\n\n---\n\n' + combined : combined }));
          })}

          <div className="space-y-4 mt-2">
            {/* Description */}
            <div>
              <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Descrierea situației *</label>
              <textarea rows={4} value={strategyInput.description}
                onChange={e => setStrategyInput(p => ({ ...p, description: e.target.value }))}
                placeholder="Descrie situația: ce s-a întâmplat, de ce vrei să contești, ce clauze/decizii sunt problematice..."
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm resize-none focus:ring-2 focus:ring-indigo-500/40 outline-none" />
              <label className="mt-1 inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 cursor-pointer font-medium">
                <FolderInput size={12} />
                <span>Auto-completează din document</span>
                <input type="file" accept=".pdf,.docx,.txt,.md" className="hidden"
                  onChange={e => { if (e.target.files?.[0]) extractEntitiesFromFile(e.target.files[0], 'strategy'); }} />
              </label>
            </div>

            {/* Criticism codes multi-select */}
            <div>
              <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Coduri critică *</label>
              {strategyInput.coduri_critici.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {strategyInput.coduri_critici.map(code => (
                    <span key={code} className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-100 text-indigo-800 rounded-full text-xs font-medium">
                      {code}
                      <button onClick={() => setStrategyInput(p => ({ ...p, coduri_critici: p.coduri_critici.filter(c => c !== code) }))} className="hover:text-red-600">&times;</button>
                    </span>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-1 gap-0.5 max-h-36 overflow-y-auto border border-slate-200 rounded-lg p-2 bg-white">
                {analyticsFilterOptions.critici.map((c: any) => {
                  const selected = strategyInput.coduri_critici.includes(c.code);
                  const legend = (CRITIQUE_LEGEND as any)[c.code] || c.code;
                  return (
                    <label key={c.code} className={`flex items-center gap-2 px-2 py-1 rounded cursor-pointer hover:bg-slate-50 text-xs ${selected ? 'bg-indigo-50' : ''}`}>
                      <input type="checkbox" checked={selected} onChange={() => {
                        setStrategyInput(p => ({
                          ...p,
                          coduri_critici: selected ? p.coduri_critici.filter(x => x !== c.code) : [...p.coduri_critici, c.code],
                        }));
                      }} className="rounded border-slate-300" />
                      <span className="font-mono font-bold text-slate-700 w-7">{c.code}</span>
                      <span className="text-slate-600 flex-1 truncate">{(legend.split('—')[0] || legend).trim()}</span>
                      <span className="text-slate-400">({c.count})</span>
                    </label>
                  );
                })}
              </div>
            </div>

            {/* Dropdowns row */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Complet CNSC</label>
                <select value={strategyInput.complet}
                  onChange={e => setStrategyInput(p => ({ ...p, complet: e.target.value }))}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white">
                  <option value="">— Necunoscut —</option>
                  {analyticsFilterOptions.complete.map((c: any) => (
                    <option key={c.name} value={c.name}>{c.name} ({c.count})</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Tip contestație</label>
                <select value={strategyInput.tip_contestatie}
                  onChange={e => setStrategyInput(p => ({ ...p, tip_contestatie: e.target.value }))}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white">
                  <option value="">— Selectează —</option>
                  <option value="documentatie">Documentație</option>
                  <option value="rezultat">Rezultat</option>
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Tip procedură</label>
                <select value={strategyInput.tip_procedura}
                  onChange={e => setStrategyInput(p => ({ ...p, tip_procedura: e.target.value }))}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white">
                  <option value="">— Toate —</option>
                  {analyticsFilterOptions.proceduri.map((p: any) => (
                    <option key={p.name} value={p.name}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Cod CPV</label>
                <input type="text" value={strategyInput.cod_cpv}
                  onChange={e => setStrategyInput(p => ({ ...p, cod_cpv: e.target.value }))}
                  placeholder="ex: 45310000-3" className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-600 uppercase mb-1">Valoare estimată (RON)</label>
              <input type="text" value={strategyInput.valoare_estimata}
                onChange={e => setStrategyInput(p => ({ ...p, valoare_estimata: e.target.value }))}
                placeholder="ex: 500000" className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm" />
            </div>

            <button onClick={runStrategy}
              disabled={strategyLoading || strategyInput.coduri_critici.length === 0 || !strategyInput.description.trim()}
              className="w-full bg-indigo-600 text-white py-3 rounded-xl font-semibold hover:bg-indigo-700 disabled:opacity-50 transition flex items-center justify-center gap-2 text-sm">
              {strategyLoading ? <><Loader2 className="w-4 h-4 animate-spin" /> Se generează strategia...</> : <><Target size={16} /> Generează Strategie</>}
            </button>
          </div>
        </div>

        {/* Right panel — results */}
        <div className="w-full md:w-2/3 p-4 md:p-8 overflow-y-auto bg-white">
          {!strategyResult && !strategyLoading && (
            <div className="h-full flex items-center justify-center text-slate-400">
              <div className="text-center">
                <Target size={48} className="mx-auto mb-4 opacity-30" />
                <p className="text-lg font-medium">Generare Strategie Contestare</p>
                <p className="text-sm mt-1">Completează formularul și apasă Generează</p>
              </div>
            </div>
          )}
          {strategyLoading && (
            <div className="h-full flex items-center justify-center">
              <div className="text-center">
                <Loader2 size={40} className="animate-spin text-indigo-500 mx-auto mb-4" />
                <p className="text-slate-600 font-medium">Se analizează statistici, legislație și jurisprudență...</p>
                <p className="text-xs text-slate-400 mt-1">Poate dura 30-60 secunde</p>
              </div>
            </div>
          )}
          {strategyResult && (
            <div className="max-w-3xl mx-auto space-y-6">
              {/* Toolbar */}
              <div className="flex justify-end gap-3">
                <button onClick={() => {
                  const fullText = (strategyResult.overall_assessment?.text || '') + '\n\n' +
                    (strategyResult.per_criticism || []).map((r: any) =>
                      `## ${r.code} — ${r.label}\n${r.recommendation || ''}\n\nArgumente: ${(r.arguments || []).join('; ')}\nTemei legal: ${(r.legal_basis || []).join('; ')}\nProbabilitate: ${r.success_probability}%`
                    ).join('\n\n');
                  const refs = (strategyResult.precedents || []).map((p: any) => p.bo_reference);
                  saveDocument('strategie', strategyInput.description.slice(0, 200) || 'Strategie contestare', fullText, refs, {
                    coduri_critici: strategyInput.coduri_critici,
                    cod_cpv: strategyInput.cod_cpv,
                    complet: strategyInput.complet,
                    probability: strategyResult.overall_assessment?.overall_probability,
                  });
                }} className="text-sm text-green-600 font-medium hover:underline flex items-center gap-1"><Save size={12} /> Salvează</button>
              </div>
              {/* Overall assessment */}
              <div className={`rounded-2xl p-6 ${strategyResult.overall_assessment?.recommendation === 'ADMIS' ? 'bg-green-50 border-2 border-green-300' : 'bg-red-50 border-2 border-red-300'}`}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-lg font-bold text-slate-800">Evaluare Generală</h3>
                  <div className={`text-2xl font-black ${strategyResult.overall_assessment?.recommendation === 'ADMIS' ? 'text-green-700' : 'text-red-700'}`}>
                    {strategyResult.overall_assessment?.overall_probability}% {strategyResult.overall_assessment?.recommendation}
                  </div>
                </div>
                <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(strategyResult.overall_assessment?.text || '') }} />
              </div>

              {/* Per-criticism recommendations */}
              <div>
                <h3 className="text-lg font-bold text-slate-800 mb-3">Recomandări per Critică</h3>
                <div className="space-y-4">
                  {(strategyResult.per_criticism || []).map((rec: any, i: number) => (
                    <div key={i} className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
                      <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-black text-indigo-700 text-lg">{rec.code}</span>
                          <span className="text-sm text-slate-500">{rec.label}</span>
                        </div>
                        {rec.success_probability != null && (
                          <span className={`px-3 py-1 rounded-full text-sm font-bold ${rec.success_probability >= 60 ? 'bg-green-100 text-green-800' : rec.success_probability >= 40 ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-800'}`}>
                            {rec.success_probability}%
                          </span>
                        )}
                      </div>
                      {rec.recommendation && (
                        <p className="text-sm text-slate-700 mb-3 font-medium">{rec.recommendation}</p>
                      )}
                      {rec.arguments?.length > 0 && (
                        <div className="mb-3">
                          <p className="text-xs font-bold text-slate-500 uppercase mb-1">Argumente cheie</p>
                          <ul className="text-sm text-slate-700 space-y-1">
                            {rec.arguments.map((arg: string, j: number) => (
                              <li key={j} className="flex gap-2"><span className="text-green-500 mt-0.5">&#10003;</span>{arg}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {rec.legal_basis?.length > 0 && (
                        <div className="mb-3">
                          <p className="text-xs font-bold text-slate-500 uppercase mb-1">Temei legal</p>
                          <div className="flex flex-wrap gap-1">
                            {rec.legal_basis.map((ref: string, j: number) => (
                              <span key={j} className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded border border-blue-200">{ref}</span>
                            ))}
                          </div>
                        </div>
                      )}
                      {rec.useful_precedents?.length > 0 && (
                        <div className="mb-3">
                          <p className="text-xs font-bold text-slate-500 uppercase mb-1">Jurisprudență utilă</p>
                          <div className="space-y-1">
                            {rec.useful_precedents.map((prec: string, j: number) => (
                              <p key={j} className="text-xs text-slate-600">{prec}</p>
                            ))}
                          </div>
                        </div>
                      )}
                      {rec.risks?.length > 0 && (
                        <div>
                          <p className="text-xs font-bold text-slate-500 uppercase mb-1">Riscuri</p>
                          <ul className="text-xs text-red-700 space-y-0.5">
                            {rec.risks.map((risk: string, j: number) => (
                              <li key={j} className="flex gap-1"><span>&#9888;</span>{risk}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {/* Stats badge */}
                      {rec.stats?.total > 0 && (
                        <div className="mt-3 pt-3 border-t border-slate-100 text-xs text-slate-400">
                          Bazat pe {rec.stats.total} cazuri judecate pe fond | Contestator câștigă {rec.stats.win_rate}%
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Precedents */}
              {strategyResult.precedents?.length > 0 && (
                <div className="bg-blue-50 border border-blue-200 rounded-xl p-5">
                  <h3 className="font-semibold text-blue-800 mb-3">Jurisprudență CNSC Relevantă</h3>
                  <div className="flex flex-wrap gap-2">
                    {strategyResult.precedents.map((p: any, i: number) => (
                      <span key={i} onClick={() => openDecision(p.bo_reference)}
                        className="text-xs bg-white text-blue-700 px-3 py-1.5 rounded-lg border border-blue-200 font-mono cursor-pointer hover:bg-blue-100 transition">
                        {p.bo_reference} ({p.solutie}) — {p.cod_critica} ({p.castigator})
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Legal basis */}
              {strategyResult.legal_basis?.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-5">
                  <h3 className="font-semibold text-amber-800 mb-3">Temei Legal Utilizat</h3>
                  <div className="space-y-2">
                    {strategyResult.legal_basis.map((ref: any, i: number) => (
                      <div key={i} className="text-sm">
                        <span className="font-mono font-bold text-amber-800">{ref.citare}</span>
                        <span className="text-amber-600 ml-1">({ref.act})</span>
                        <p className="text-xs text-amber-700 mt-0.5">{ref.text?.slice(0, 200)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderAnalytics = () => {
    // Load panels on first visit
    if (panelsList.length === 0 && !panelsLoading) loadPanelsList();

    return (
      <div className="h-full overflow-y-auto">
        <div className="p-4 md:p-8 max-w-7xl mx-auto">
          <h1 className="text-2xl font-bold text-slate-800 mb-2">Analiză CNSC</h1>
          <p className="text-slate-500 mb-6">Profiluri complete, predictor rezultat și comparare decizii</p>

          {/* Tab Navigation */}
          <div className="flex gap-2 mb-6 border-b border-slate-200 pb-2">
            {[
              { key: 'panels' as const, label: 'Profiluri Complete', icon: '⚖️' },
              { key: 'predictor' as const, label: 'Predictor Rezultat', icon: '🎯' },
              { key: 'compare' as const, label: 'Comparare Decizii', icon: '🔀' },
            ].map(tab => (
              <button key={tab.key} onClick={() => setAnalyticsTab(tab.key)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${analyticsTab === tab.key ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100'}`}>
                {tab.icon} {tab.label}
              </button>
            ))}
          </div>

          {/* PANELS TAB */}
          {analyticsTab === 'panels' && (
            <div>
              {!selectedPanel ? (
                <div>
                  <h2 className="text-lg font-semibold text-slate-700 mb-4">Complete CNSC (C1-C20)</h2>
                  {panelsLoading ? (
                    <div className="flex items-center gap-2 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /> Se încarcă...</div>
                  ) : (
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
                      {panelsList.map((p: any) => (
                        <button key={p.complet} onClick={() => loadPanelProfile(p.complet)}
                          className="bg-white border border-slate-200 rounded-xl p-4 hover:border-blue-400 hover:shadow-md transition-all text-left">
                          <div className="text-lg font-bold text-blue-700">{p.complet}</div>
                          <div className="text-2xl font-black text-slate-800">{p.total}</div>
                          <div className="text-xs text-slate-500">decizii</div>
                          <div className="mt-2 flex items-center gap-1">
                            <div className="flex-1 bg-slate-100 rounded-full h-2 overflow-hidden">
                              <div className="bg-green-500 h-2 rounded-full" style={{ width: `${p.win_rate}%` }} />
                            </div>
                            <span className="text-xs font-semibold text-green-700">{p.win_rate}%</span>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <button onClick={() => { setSelectedPanel(null); setPanelProfile(null); }}
                    className="text-blue-600 hover:text-blue-800 text-sm mb-4 flex items-center gap-1">
                    ← Înapoi la lista completelor
                  </button>
                  {panelsLoading || !panelProfile ? (
                    <div className="flex items-center gap-2 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /> Se încarcă profilul {selectedPanel}...</div>
                  ) : (
                    <div>
                      <h2 className="text-xl font-bold text-slate-800 mb-4">Completul {panelProfile.complet}</h2>

                      {/* Summary cards */}
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
                        <div className="bg-blue-50 p-4 rounded-xl">
                          <div className="text-xs text-blue-600 font-medium">Total Decizii</div>
                          <div className="text-2xl font-black text-blue-800">{panelProfile.total_decisions}</div>
                        </div>
                        <div className="bg-slate-50 p-4 rounded-xl">
                          <div className="text-xs text-slate-500 font-medium">Judecate pe fond</div>
                          <div className="text-2xl font-black text-slate-700">{panelProfile.total_pe_fond || panelProfile.total_decisions}</div>
                          {panelProfile.procedural_exclusions > 0 && (
                            <div className="text-xs text-slate-400 mt-1">{panelProfile.procedural_exclusions} excluse (procedurale)</div>
                          )}
                        </div>
                        <div className="bg-green-50 p-4 rounded-xl">
                          <div className="text-xs text-green-600 font-medium">Rată Admitere</div>
                          <div className="text-2xl font-black text-green-800">{panelProfile.win_rate}%</div>
                        </div>
                        <div className="bg-emerald-50 p-4 rounded-xl">
                          <div className="text-xs text-emerald-600 font-medium">ADMIS</div>
                          <div className="text-2xl font-black text-emerald-800">{(panelProfile.rulings?.ADMIS || 0) + (panelProfile.rulings?.ADMIS_PARTIAL || 0)}</div>
                        </div>
                        <div className="bg-red-50 p-4 rounded-xl">
                          <div className="text-xs text-red-600 font-medium">RESPINS (pe fond)</div>
                          <div className="text-2xl font-black text-red-800">{panelProfile.rulings?.RESPINS || 0}</div>
                        </div>
                      </div>

                      {/* By type */}
                      <div className="bg-white border border-slate-200 rounded-xl p-5 mb-6">
                        <h3 className="font-semibold text-slate-700 mb-3">Pe tip contestație</h3>
                        <div className="space-y-2">
                          {panelProfile.by_type?.map((t: any) => (
                            <div key={t.type} className="flex items-center gap-3">
                              <span className="text-sm font-medium text-slate-600 w-28">{t.type}</span>
                              <div className="flex-1 bg-slate-100 rounded-full h-3 overflow-hidden">
                                <div className="bg-blue-500 h-3 rounded-full transition-all" style={{ width: `${t.win_rate}%` }} />
                              </div>
                              <span className="text-sm font-semibold w-16 text-right">{t.win_rate}%</span>
                              <span className="text-xs text-slate-400 w-20 text-right">({t.total} dec.)</span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Criticism code tendencies */}
                      <div className="bg-white border border-slate-200 rounded-xl p-5 mb-6">
                        <h3 className="font-semibold text-slate-700 mb-3">Tendințe per cod de critică</h3>
                        <div className="space-y-2">
                          {panelProfile.criticism_stats?.slice(0, 10).map((c: any) => (
                            <div key={c.code} className="flex items-center gap-3">
                              <span className="text-sm font-mono font-bold text-slate-700 w-12">{c.code}</span>
                              <div className="flex-1 bg-slate-100 rounded-full h-3 overflow-hidden">
                                <div className="bg-green-500 h-3 rounded-full" style={{ width: `${c.contestator_win_rate}%` }} />
                              </div>
                              <span className="text-sm font-semibold w-16 text-right text-green-700">{c.contestator_win_rate}%</span>
                              <span className="text-xs text-slate-400 w-20 text-right">({c.total} cazuri)</span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Top CPV domains */}
                      {panelProfile.top_cpv?.length > 0 && (
                        <div className="bg-white border border-slate-200 rounded-xl p-5 mb-6">
                          <h3 className="font-semibold text-slate-700 mb-3">Domenii CPV frecvente</h3>
                          <div className="space-y-2">
                            {panelProfile.top_cpv.slice(0, 8).map((c: any) => (
                              <div key={c.cpv_group} className="flex items-center gap-3">
                                <span className="text-sm font-mono text-slate-600 w-12">{c.cpv_group}*</span>
                                <span className="text-xs text-slate-500 flex-1">{c.categorie || ''}</span>
                                <span className="text-sm font-semibold text-green-700">{c.win_rate}%</span>
                                <span className="text-xs text-slate-400">({c.total})</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Yearly trend */}
                      {panelProfile.yearly_trend?.length > 0 && (
                        <div className="bg-white border border-slate-200 rounded-xl p-5">
                          <h3 className="font-semibold text-slate-700 mb-3">Evoluție anuală</h3>
                          <div className="flex items-end gap-2 h-32">
                            {panelProfile.yearly_trend.map((y: any) => {
                              const maxTotal = Math.max(...panelProfile.yearly_trend.map((t: any) => t.total));
                              const h = maxTotal > 0 ? (y.total / maxTotal) * 100 : 0;
                              return (
                                <div key={y.year} className="flex-1 flex flex-col items-center gap-1">
                                  <div className="text-xs font-semibold text-green-700">{y.win_rate}%</div>
                                  <div className="w-full bg-slate-100 rounded-t-md overflow-hidden" style={{ height: `${h}%`, minHeight: '4px' }}>
                                    <div className="bg-blue-500 w-full h-full rounded-t-md" />
                                  </div>
                                  <div className="text-xs text-slate-500">{y.year}</div>
                                  <div className="text-xs text-slate-400">{y.total}</div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* PREDICTOR TAB */}
          {analyticsTab === 'predictor' && (() => {
            // Load filter options on first render
            if (analyticsFilterOptions.critici.length === 0) loadAnalyticsFilters();
            return (
            <div className="max-w-2xl">
              <h2 className="text-lg font-semibold text-slate-700 mb-4">Predictor Rezultat Contestație</h2>
              <p className="text-sm text-slate-500 mb-6">Selectează parametrii cazului pentru a estima probabilitatea de admitere pe baza statisticilor istorice CNSC (excluse respingerile procedurale).</p>
              <div className="space-y-4 bg-white border border-slate-200 rounded-xl p-5 mb-6">
                {/* Criticism codes multi-select */}
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">Coduri critică *</label>
                  {predictorInput.coduri_critici.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {predictorInput.coduri_critici.map(code => (
                        <span key={code} className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-100 text-blue-800 rounded-full text-xs font-medium">
                          {code}
                          <button onClick={() => setPredictorInput(p => ({ ...p, coduri_critici: p.coduri_critici.filter(c => c !== code) }))} className="hover:text-red-600">×</button>
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="grid grid-cols-1 gap-1 max-h-48 overflow-y-auto border border-slate-200 rounded-lg p-2">
                    {analyticsFilterOptions.critici.map((c: any) => {
                      const selected = predictorInput.coduri_critici.includes(c.code);
                      const legend = (CRITIQUE_LEGEND as any)[c.code] || c.code;
                      // Strip redundant prefix from legend (D/R already indicates type)
                      const shortLegend = legend.split('—')[0]?.trim() || legend.split(' - ')[0]?.trim() || legend;
                      return (
                        <label key={c.code} className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer hover:bg-slate-50 text-sm ${selected ? 'bg-blue-50' : ''}`}>
                          <input type="checkbox" checked={selected} onChange={() => {
                            setPredictorInput(p => ({
                              ...p,
                              coduri_critici: selected ? p.coduri_critici.filter(x => x !== c.code) : [...p.coduri_critici, c.code],
                            }));
                          }} className="rounded border-slate-300" />
                          <span className="font-mono font-bold text-slate-700 w-8">{c.code}</span>
                          <span className="text-slate-600 flex-1 truncate">{shortLegend}</span>
                          <span className="text-xs text-slate-400">({c.count})</span>
                        </label>
                      );
                    })}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  {/* CPV searchable */}
                  <div className="relative">
                    <label className="block text-sm font-medium text-slate-700 mb-1">Cod CPV</label>
                    <input type="text" value={predictorInput.cod_cpv || cpvPredictorSearch}
                      onChange={async (e) => {
                        const val = e.target.value;
                        setCpvPredictorSearch(val);
                        setPredictorInput(p => ({ ...p, cod_cpv: val }));
                        if (val.length >= 2) {
                          try {
                            const res = await authFetch(`/api/v1/decisions/filters/cpv-codes?search=${encodeURIComponent(val)}`);
                            if (res.ok) setCpvPredictorResults(await res.json());
                          } catch {}
                        } else {
                          setCpvPredictorResults([]);
                        }
                      }}
                      placeholder="Caută CPV..." className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm" />
                    {cpvPredictorResults.length > 0 && cpvPredictorSearch.length >= 2 && (
                      <div className="absolute z-10 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-40 overflow-y-auto">
                        {cpvPredictorResults.slice(0, 10).map((c: any) => (
                          <button key={c.code || c.cod_cpv} onClick={() => {
                            setPredictorInput(p => ({ ...p, cod_cpv: c.code || c.cod_cpv }));
                            setCpvPredictorSearch('');
                            setCpvPredictorResults([]);
                          }} className="w-full text-left px-3 py-1.5 text-xs hover:bg-blue-50 border-b border-slate-50">
                            <span className="font-mono font-bold">{c.code || c.cod_cpv}</span> {c.description || c.descriere || ''}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Complet dropdown */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1">Complet CNSC</label>
                    <select value={predictorInput.complet}
                      onChange={e => setPredictorInput(p => ({ ...p, complet: e.target.value }))}
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white">
                      <option value="">— Toate —</option>
                      {analyticsFilterOptions.complete.map((c: any) => (
                        <option key={c.name} value={c.name}>{c.name} ({c.count} dec.)</option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Tip procedura dropdown */}
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1">Tip procedură</label>
                  <select value={predictorInput.tip_procedura}
                    onChange={e => setPredictorInput(p => ({ ...p, tip_procedura: e.target.value }))}
                    className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white">
                    <option value="">— Toate —</option>
                    {analyticsFilterOptions.proceduri.map((p: any) => (
                      <option key={p.name} value={p.name}>{p.name} ({p.count})</option>
                    ))}
                  </select>
                </div>

                <button onClick={runPrediction} disabled={predictorLoading || predictorInput.coduri_critici.length === 0}
                  className="w-full bg-blue-600 text-white py-2.5 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2">
                  {predictorLoading ? <><Loader2 className="w-4 h-4 animate-spin" /> Se calculează...</> : 'Generează Predicție'}
                </button>
              </div>

              {predictorResult && (
                <div className="space-y-4">
                  {/* Main prediction */}
                  <div className={`rounded-xl p-6 text-center ${predictorResult.prediction.outcome === 'ADMIS' ? 'bg-green-50 border-2 border-green-300' : 'bg-red-50 border-2 border-red-300'}`}>
                    <div className="text-sm font-medium text-slate-600 mb-1">Predicție</div>
                    <div className={`text-3xl font-black ${predictorResult.prediction.outcome === 'ADMIS' ? 'text-green-700' : 'text-red-700'}`}>
                      {predictorResult.prediction.outcome}
                    </div>
                    <div className="text-lg font-bold mt-1">{predictorResult.prediction.probability}% probabilitate</div>
                    <div className="text-xs text-slate-500 mt-1">Încredere: {(predictorResult.prediction.confidence * 100).toFixed(0)}%</div>
                  </div>

                  {/* Dimension stats */}
                  <div className="bg-white border border-slate-200 rounded-xl p-5">
                    <h3 className="font-semibold text-slate-700 mb-3">Statistici pe dimensiuni</h3>
                    <div className="space-y-3">
                      {Object.entries(predictorResult.stats).map(([key, s]: [string, any]) => (
                        <div key={key} className="flex items-center gap-3">
                          <span className="text-sm font-medium text-slate-600 w-32 truncate">{key.replace('critica_', 'Critica ').replace('cpv_domain', 'Domeniu CPV').replace('panel', 'Complet').replace('procedure', 'Procedură')}</span>
                          <div className="flex-1 bg-slate-100 rounded-full h-3 overflow-hidden">
                            <div className={`h-3 rounded-full ${s.win_rate >= 50 ? 'bg-green-500' : 'bg-red-400'}`}
                              style={{ width: `${s.win_rate}%` }} />
                          </div>
                          <span className="text-sm font-semibold w-14 text-right">{s.win_rate}%</span>
                          <span className="text-xs text-slate-400 w-16 text-right">({s.total || s.total_cases || 0})</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* LLM reasoning */}
                  {predictorResult.reasoning && (
                    <div className="bg-amber-50 border border-amber-200 rounded-xl p-5">
                      <h3 className="font-semibold text-amber-800 mb-2">Analiză AI</h3>
                      <div className="text-sm text-amber-900 prose prose-sm prose-amber max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(predictorResult.reasoning) }} />
                    </div>
                  )}
                </div>
              )}
            </div>
          ); })()}

          {/* COMPARE TAB */}
          {analyticsTab === 'compare' && (
            <div>
              <h2 className="text-lg font-semibold text-slate-700 mb-4">Comparare Decizii</h2>
              <p className="text-sm text-slate-500 mb-6">Introdu referințele BO ale a 2-3 decizii pentru o comparație detaliată cu analiză AI.</p>
              <div className="bg-white border border-slate-200 rounded-xl p-5 mb-6">
                <div className="space-y-3">
                  {compareIds.map((id, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-600 w-20">Decizia {i + 1}{i < 2 ? ' *' : ''}</span>
                      <input type="text" value={id}
                        onChange={e => { const n = [...compareIds]; n[i] = e.target.value; setCompareIds(n); }}
                        placeholder="ex: BO2025_1011" className="flex-1 border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono" />
                    </div>
                  ))}
                  {compareIds.length < 3 && (
                    <button onClick={() => setCompareIds([...compareIds, ''])}
                      className="text-blue-600 text-sm hover:text-blue-800">+ Adaugă decizie</button>
                  )}
                </div>
                <button onClick={runComparison} disabled={compareLoading || compareIds.filter(Boolean).length < 2}
                  className="mt-4 w-full bg-blue-600 text-white py-2.5 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2">
                  {compareLoading ? <><Loader2 className="w-4 h-4 animate-spin" /> Se analizează...</> : 'Compară Decizii'}
                </button>
              </div>

              {compareResult && (
                <div className="space-y-4">
                  {/* Metadata comparison table */}
                  <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="bg-slate-50">
                          <tr>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500">Câmp</th>
                            {compareResult.decisions.map((d: any) => (
                              <th key={d.id} className="px-4 py-3 text-left text-xs font-semibold text-slate-700">{d.numar_bo}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {['complet', 'solutie', 'tip_contestatie', 'tip_procedura', 'criteriu_atribuire', 'cod_cpv', 'data_decizie'].map(field => (
                            <tr key={field} className="hover:bg-slate-50">
                              <td className="px-4 py-2 text-xs font-medium text-slate-500">{field}</td>
                              {compareResult.decisions.map((d: any) => (
                                <td key={d.id} className={`px-4 py-2 text-xs ${field === 'solutie' ? (d[field] === 'ADMIS' ? 'text-green-700 font-bold' : d[field] === 'RESPINS' ? 'text-red-700 font-bold' : 'text-amber-700 font-bold') : 'text-slate-700'}`}>
                                  {d[field] || '—'}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Per-decision argumentation */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {compareResult.decisions.map((d: any) => (
                      <div key={d.id} className="bg-white border border-slate-200 rounded-xl p-4">
                        <div className="flex items-center justify-between mb-3">
                          <span className="font-bold text-slate-800">{d.numar_bo}</span>
                          <span className={`px-2 py-0.5 rounded text-xs font-bold ${d.solutie === 'ADMIS' ? 'bg-green-100 text-green-800' : d.solutie === 'RESPINS' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'}`}>
                            {d.solutie}
                          </span>
                        </div>
                        {d.rezumat && <p className="text-xs text-slate-600 mb-3">{d.rezumat}</p>}
                        <div className="space-y-2">
                          {d.argumentari?.map((a: any, i: number) => (
                            <div key={i} className="border-l-2 border-slate-300 pl-3">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-xs font-mono font-bold text-slate-700">{a.cod_critica}</span>
                                <span className={`text-xs px-1.5 py-0.5 rounded ${a.castigator === 'contestator' ? 'bg-green-100 text-green-700' : a.castigator === 'autoritate' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-600'}`}>
                                  {a.castigator}
                                </span>
                              </div>
                              {a.argumentatie_cnsc && (
                                <p className="text-xs text-slate-500 line-clamp-3">{a.argumentatie_cnsc}</p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* LLM analysis */}
                  {compareResult.analysis && (
                    <div className="bg-blue-50 border border-blue-200 rounded-xl p-5">
                      <h3 className="font-semibold text-blue-800 mb-3">Analiză Comparativă AI</h3>
                      <div className="text-sm text-blue-900 prose prose-sm prose-blue max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(compareResult.analysis) }} />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderTraining = () => {
    const effectiveResult = trainingEditedResult ?? trainingResult;
    const sections = effectiveResult ? parseTrainingSections(effectiveResult) : {};
    const activeContent = sections[trainingActiveTab] || '';

    return (
      <div className="h-full flex flex-col md:flex-row bg-white">
        {/* Left panel — form */}
        <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
          <h2 className="text-lg font-bold text-slate-800 mb-4 flex gap-2 items-center">
            <GraduationCap className="text-amber-600" size={20}/>
            TrainingAP — Materiale Didactice
          </h2>
          <ScopeSelector compact />
          <ActiveScopeIndicator />

          {renderActiveDosarBanner((docs) => {
            setUploadedDocsTrainingContext(prev => [...prev, ...docs]);
          })}

          <div className="space-y-5 mt-2">
            {/* Mode selector */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Mod Generare</label>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { key: 'individual' as const, label: 'Individual', desc: '1 material' },
                  { key: 'batch' as const, label: 'Lot (Batch)', desc: 'Mai multe' },
                  { key: 'program' as const, label: 'Program Formare', desc: 'Complet' },
                ].map(({ key, label, desc }) => (
                  <button
                    key={key}
                    onClick={() => setTrainingMode(key)}
                    className={`py-2 px-2 rounded-lg text-xs font-medium transition border text-center ${
                      trainingMode === key
                        ? 'bg-amber-600 text-white border-amber-600 shadow-md'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-amber-400'
                    }`}
                  >
                    <div>{label}</div>
                    <div className="opacity-70 text-[10px]">{desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Tema */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                {trainingMode === 'program' ? 'Tema Programului de Formare' : 'Tema / Subiectul'}
              </label>
              <textarea
                className={`w-full p-3 border rounded-lg text-sm h-24 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingTema.length > 20000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                placeholder={trainingMode === 'program'
                  ? "Ex: Program de formare pentru evaluarea ofertelor — 4 module, 2 zile..."
                  : "Ex: Evaluarea ofertelor în procedura de licitație deschisă, Termenele de contestare, Criteriul prețul cel mai scăzut vs. cel mai bun raport calitate-preț..."}
                value={trainingTema}
                onChange={(e) => setTrainingTema(e.target.value)}
              />
              <CharCounter value={trainingTema} maxLength={20000} />
            </div>

            {/* Public țintă (opțional) */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Public Țintă (opțional)</label>
              <textarea
                className={`w-full p-3 border rounded-lg text-sm h-16 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingPublicTinta.length > 5000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                placeholder="Ex: Reprezentanți autorități contractante, Reprezentanți operatori economici, Reprezentanți autoritatea de audit / Curtea de Conturi / organe de control, Reprezentanți CNSC, Consultanți achiziții publice..."
                value={trainingPublicTinta}
                onChange={(e) => setTrainingPublicTinta(e.target.value)}
              />
              {trainingPublicTinta.length > 100 && <CharCounter value={trainingPublicTinta} maxLength={5000} />}
            </div>

            {/* Tip material — single select for individual, multi-select for batch/program */}
            {trainingMode === 'individual' ? (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Tip Material</label>
                <select
                  className="w-full p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm bg-white"
                  value={trainingTip}
                  onChange={(e) => setTrainingTip(e.target.value)}
                >
                  {Object.entries(trainingMaterialTypes).map(([key, val]) => (
                    <option key={key} value={key}>{val.name} — {val.desc}</option>
                  ))}
                </select>
              </div>
            ) : (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                  Tipuri Materiale {trainingMode === 'batch' ? '(se alternează)' : '(selectează tipurile dorite)'}
                </label>
                <div className="grid grid-cols-2 gap-1.5">
                  {Object.entries(trainingMaterialTypes).map(([key, val]) => {
                    const isSelected = trainingSelectedTypes.includes(key);
                    return (
                      <button
                        key={key}
                        onClick={() => toggleTrainingType(key)}
                        className={`flex items-center gap-2 py-1.5 px-2.5 rounded-lg text-xs font-medium transition border text-left ${
                          isSelected
                            ? 'bg-amber-50 text-amber-700 border-amber-300'
                            : 'bg-white text-slate-500 border-slate-200 hover:border-amber-300'
                        }`}
                      >
                        <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 ${
                          isSelected ? 'bg-amber-500 border-amber-500 text-white' : 'border-slate-300'
                        }`}>
                          {isSelected && <CheckSquare size={10} />}
                        </span>
                        <span className="truncate">{val.name}</span>
                      </button>
                    );
                  })}
                </div>
                {trainingSelectedTypes.length === 0 && (
                  <p className="text-[10px] text-amber-600 mt-1">
                    {trainingMode === 'batch'
                      ? 'Selectează cel puțin un tip. Se va folosi tipul implicit.'
                      : 'Dacă nu selectezi nimic, LLM-ul va alege automat.'}
                  </p>
                )}
              </div>
            )}

            {/* Batch count — only in batch mode */}
            {trainingMode === 'batch' && (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Număr Materiale</label>
                <div className="flex items-center gap-2">
                  {!trainingBatchCustom ? (
                    <select
                      className="flex-1 p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm bg-white"
                      value={trainingBatchCount}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === 'custom') { setTrainingBatchCustom(true); }
                        else setTrainingBatchCount(Number(v));
                      }}
                    >
                      {Array.from({ length: 10 }, (_, i) => i + 1).map(n => (
                        <option key={n} value={n}>{n} {n === 1 ? 'material' : 'materiale'}</option>
                      ))}
                      <option value="custom">Altă valoare...</option>
                    </select>
                  ) : (
                    <div className="flex-1 flex items-center gap-2">
                      <input
                        type="number"
                        min={1}
                        max={50}
                        className="flex-1 p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm"
                        value={trainingBatchCount}
                        onChange={(e) => setTrainingBatchCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                      />
                      <button
                        onClick={() => setTrainingBatchCustom(false)}
                        className="text-xs text-amber-600 hover:text-amber-800 font-medium whitespace-nowrap"
                      >
                        ← Lista
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Program plan — only in program mode */}
            {trainingMode === 'program' && (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Plan / Program de Formare (opțional)</label>
                <div className="bg-slate-50 p-3 rounded-lg border border-dashed border-slate-300 mb-2">
                  <input
                    type="file"
                    accept=".txt,.md,.pdf,.doc,.docx"
                    onChange={(e) => handleDocumentUpload(e, (text) => setTrainingProgramPlan(prev => prev ? prev + '\n\n---\n\n' + text : text), (doc) => setUploadedDocsTrainingPlan(prev => [...prev, doc]))}
                    className="block w-full text-sm text-slate-600 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-amber-50 file:text-amber-700 hover:file:bg-amber-100"
                  />
                  {uploadedDocsTrainingPlan.length > 0 && (
                    <div className="mt-1 space-y-1">
                      {uploadedDocsTrainingPlan.map((doc, idx) => (
                        <div key={idx} className="flex items-center justify-between text-xs text-green-600 bg-green-50 rounded px-2 py-1">
                          <span>✓ {doc.name} ({doc.text.length.toLocaleString()} car.)</span>
                          <button onClick={() => setUploadedDocsTrainingPlan(prev => prev.filter((_, i) => i !== idx))} className="text-red-400 hover:text-red-600 ml-2" title="Șterge">✕</button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <textarea
                  className={`w-full p-3 border rounded-lg text-sm h-24 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingProgramPlan.length > 50000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                  placeholder="Lipește sau descrie programul de formare: module, tematici, competențe vizate, durată..."
                  value={trainingProgramPlan}
                  onChange={(e) => setTrainingProgramPlan(e.target.value)}
                />
                {trainingProgramPlan.length > 100 && <CharCounter value={trainingProgramPlan} maxLength={50000} />}
                <p className="text-[10px] text-slate-400 mt-1">LLM-ul va alege automat cele mai potrivite tipuri de materiale pentru fiecare tematică din program.</p>
              </div>
            )}

            {/* Nivel dificultate */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Nivel Dificultate</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: 'usor', label: 'Ușor' },
                  { key: 'mediu', label: 'Mediu' },
                  { key: 'dificil', label: 'Dificil' },
                  { key: 'foarte_dificil', label: 'Foarte Dificil' },
                ].map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setTrainingNivel(key)}
                    className={`py-2 px-3 rounded-lg text-sm font-medium transition border ${
                      trainingNivel === key
                        ? 'bg-amber-600 text-white border-amber-600 shadow-md'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-amber-400 hover:text-amber-700'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Lungime */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Lungime Material</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: 'scurt', label: 'Scurt', desc: '~200 cuv./secț.' },
                  { key: 'mediu', label: 'Mediu', desc: '~400 cuv./secț.' },
                  { key: 'lung', label: 'Lung', desc: '~800 cuv./secț.' },
                  { key: 'extins', label: 'Extins', desc: '~1500 cuv./secț.' },
                ].map(({ key, label, desc }) => (
                  <button
                    key={key}
                    onClick={() => setTrainingLungime(key)}
                    className={`py-2 px-3 rounded-lg text-xs font-medium transition border ${
                      trainingLungime === key
                        ? 'bg-amber-600 text-white border-amber-600 shadow-md'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-amber-400 hover:text-amber-700'
                    }`}
                  >
                    {label} <span className="opacity-70">({desc})</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Context suplimentar (collapsible) */}
            <div>
              <button
                onClick={() => setTrainingShowContext(!trainingShowContext)}
                className="flex items-center gap-2 text-xs font-bold text-slate-500 uppercase hover:text-slate-700 transition"
              >
                {trainingShowContext ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}
                Context suplimentar (opțional)
              </button>
              {trainingShowContext && (
                <div>
                  <div className="bg-slate-50 p-3 rounded-lg border border-dashed border-slate-300 mt-2 mb-2">
                    <input
                      type="file"
                      accept=".txt,.md,.pdf,.doc,.docx"
                      onChange={(e) => handleDocumentUpload(e, (text) => setTrainingContext(prev => prev ? prev + '\n\n---\n\n' + text : text), (doc) => setUploadedDocsTrainingContext(prev => [...prev, doc]))}
                      className="block w-full text-sm text-slate-600 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-amber-50 file:text-amber-700 hover:file:bg-amber-100"
                    />
                    {uploadedDocsTrainingContext.length > 0 && (
                      <div className="mt-1 space-y-1">
                        {uploadedDocsTrainingContext.map((doc, idx) => (
                          <div key={idx} className="flex items-center justify-between text-xs text-green-600 bg-green-50 rounded px-2 py-1">
                            <span>✓ {doc.name} ({doc.text.length.toLocaleString()} car.)</span>
                            <button onClick={() => setUploadedDocsTrainingContext(prev => prev.filter((_, i) => i !== idx))} className="text-red-400 hover:text-red-600 ml-2" title="Șterge">✕</button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <textarea
                    className={`w-full p-3 border rounded-lg text-sm h-20 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingContext.length > 50000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                    placeholder="Instrucțiuni adiționale, restricții, focus pe anumite aspecte..."
                    value={trainingContext}
                    onChange={(e) => setTrainingContext(e.target.value)}
                  />
                  <CharCounter value={trainingContext} maxLength={50000} />
                </div>
              )}
            </div>

            {/* Generate button */}
            <button
              onClick={handleTrainingGenerate}
              disabled={trainingLoading || !trainingTema.trim()}
              className="w-full bg-amber-600 text-white py-4 rounded-xl font-medium hover:bg-amber-700 transition flex justify-center items-center gap-2 shadow-lg hover:shadow-xl mt-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {trainingLoading ? (
                <>
                  <Loader2 className="animate-spin" size={18} />
                  {trainingBatchProgress
                    ? `Material ${trainingBatchProgress.current} / ${trainingBatchProgress.total}...`
                    : 'Generare în curs...'}
                </>
              ) : (
                <>
                  <GraduationCap size={18} />
                  {trainingMode === 'batch' ? `Generează ${trainingBatchCount} Materiale` :
                   trainingMode === 'program' ? 'Generează Program Complet' :
                   'Generează Material'}
                </>
              )}
            </button>
          </div>
        </div>

        {/* Right panel — output */}
        <div className="w-full md:w-2/3 flex flex-col overflow-hidden bg-white">
          {trainingResult ? (
            <>
              {/* Toolbar */}
              <div className="border-b border-slate-200 px-3 md:px-6 py-3 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 bg-slate-50/50">
                {/* Tabs */}
                <div className="flex gap-1 flex-wrap">
                  {[
                    { key: 'material' as const, label: 'Enunț & Cerințe' },
                    { key: 'rezolvare' as const, label: 'Rezolvare' },
                    { key: 'note' as const, label: 'Note Trainer' },
                  ].map(({ key, label }) => (
                    <button
                      key={key}
                      onClick={() => setTrainingActiveTab(key)}
                      className={`px-3 md:px-4 py-1.5 md:py-2 rounded-lg text-xs md:text-sm font-medium transition ${
                        trainingActiveTab === key
                          ? 'bg-amber-600 text-white shadow-sm'
                          : 'text-slate-600 hover:bg-slate-200'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                {/* Edit toggle + Export buttons */}
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      if (!trainingEditing) {
                        // Entering edit mode — initialize edited text from current result
                        if (trainingEditedResult === null) {
                          setTrainingEditedResult(trainingResult);
                        }
                      }
                      setTrainingEditing(!trainingEditing);
                    }}
                    disabled={trainingLoading}
                    className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition disabled:opacity-50 ${
                      trainingEditing
                        ? 'bg-amber-100 border-amber-400 text-amber-700'
                        : 'border-slate-300 text-slate-600 hover:bg-slate-100 hover:border-slate-400'
                    }`}
                  >
                    {trainingEditing ? <Eye size={12} /> : <Pencil size={12} />}
                    {trainingEditing ? 'Previzualizare' : 'Editează'}
                  </button>
                  {(['docx', 'pdf', 'md'] as const).map((fmt) => (
                    <button
                      key={fmt}
                      onClick={() => handleTrainingExport(fmt)}
                      disabled={trainingLoading}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100 hover:border-slate-400 transition disabled:opacity-50"
                    >
                      <Download size={12} />
                      {fmt.toUpperCase()}
                    </button>
                  ))}
                  <button onClick={saveTraining} disabled={trainingLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-green-300 text-green-600 hover:bg-green-50 hover:border-green-400 transition disabled:opacity-50"><Save size={12} /> Salvează</button>
                  <button onClick={() => loadHistory('training')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 text-slate-500 hover:bg-slate-50 transition"><Bookmark size={12} /> Istoric</button>
                </div>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto p-8">
                <div className="max-w-3xl mx-auto">
                  {trainingEditing ? (
                    <textarea
                      value={trainingEditedResult ?? trainingResult}
                      onChange={(e) => setTrainingEditedResult(e.target.value)}
                      className="w-full h-full min-h-[500px] p-4 text-sm font-mono border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-transparent resize-y bg-slate-50 leading-relaxed"
                      placeholder="Editează materialul generat..."
                    />
                  ) : activeContent ? (
                    <div
                      className="prose prose-slate max-w-none leading-relaxed"
                      dangerouslySetInnerHTML={{ __html: formatMarkdown(activeContent) }}
                    />
                  ) : trainingLoading ? (
                    <div className="flex items-center gap-3 text-slate-500">
                      <Loader2 className="animate-spin" size={18} />
                      <span className="text-sm">Se generează materialul...</span>
                    </div>
                  ) : (
                    <p className="text-slate-400 text-sm italic">Această secțiune nu a fost generată.</p>
                  )}
                </div>
              </div>

              {/* Footer — citations */}
              {trainingMeta && (trainingMeta.jurisprudenta_citata?.length > 0 || trainingMeta.legislatie_citata?.length > 0) && (
                <div className="border-t border-slate-200 px-6 py-3 bg-slate-50/50">
                  <div className="flex flex-wrap gap-4">
                    {trainingMeta.jurisprudenta_citata?.length > 0 && (
                      <div>
                        <span className="text-xs font-bold text-slate-500 uppercase mr-2">Jurisprudență:</span>
                        <span className="inline-flex flex-wrap gap-1">
                          {trainingMeta.jurisprudenta_citata.map((ref: string) => (
                            <span key={ref} className="text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded border border-blue-200 font-mono cursor-pointer hover:bg-blue-100 transition" onClick={() => openDecision(ref)}>{ref}</span>
                          ))}
                        </span>
                      </div>
                    )}
                    {trainingMeta.legislatie_citata?.length > 0 && (
                      <div>
                        <span className="text-xs font-bold text-slate-500 uppercase mr-2">Legislație:</span>
                        <span className="inline-flex flex-wrap gap-1">
                          {trainingMeta.legislatie_citata.map((ref: string, i: number) => (
                            <span key={i} className="text-xs bg-green-50 text-green-700 px-2 py-1 rounded border border-green-200">{ref}</span>
                          ))}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-slate-300">
              <GraduationCap size={64} className="mb-6 opacity-20" />
              <p className="text-lg font-medium text-slate-400">Configurează parametrii materialului</p>
              <p className="text-sm mt-2 text-slate-300 max-w-md text-center">
                Alege tema, tipul de material, nivelul de dificultate și lungimea dorită.
                AI-ul va genera materialul fundamentat pe legislația și jurisprudența reală din baza de date.
              </p>
            </div>
          )}
        </div>
      </div>
    );
  };

  const MemoryList = () => {
    const [items, setItems] = useState<any[]>([]);
    const [loaded, setLoaded] = useState(false);
    useEffect(() => {
      if (loaded) return;
      authFetch('/api/v1/chat/memory').then(r => r.ok ? r.json() : []).then(data => { setItems(data); setLoaded(true); }).catch(() => setLoaded(true));
    }, [loaded]);
    if (!loaded) return <p className="text-xs text-slate-400">Se încarcă...</p>;
    if (items.length === 0) return <p className="text-xs text-slate-400 italic">Nicio informație memorată încă. AI-ul va învăța din conversațiile tale.</p>;
    return (
      <div className="space-y-1.5 max-h-40 overflow-y-auto">
        {items.map((item: any) => (
          <div key={item.id} className="flex items-center justify-between bg-slate-50 rounded-lg px-3 py-1.5 text-xs">
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${item.fact_type === 'preference' ? 'bg-blue-100 text-blue-700' : item.fact_type === 'expertise' ? 'bg-green-100 text-green-700' : item.fact_type === 'case_detail' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600'}`}>
                {item.fact_type}
              </span>
              <span className="text-slate-700 truncate">{item.content}</span>
            </div>
            <button onClick={async () => {
              await authFetch(`/api/v1/chat/memory/${item.id}`, { method: 'DELETE' });
              setItems(prev => prev.filter(i => i.id !== item.id));
            }} className="text-slate-400 hover:text-red-600 ml-2 flex-shrink-0">&times;</button>
          </div>
        ))}
      </div>
    );
  };

  // --- Dosare Digitale ---
  const loadDosare = async () => {
    setDosareLoading(true);
    try {
      const res = await authFetch(`/api/v1/dosare/?status=${dosarFilter}`);
      if (res.ok) setDosare(await res.json());
    } catch { /* ignore */ }
    setDosareLoading(false);
    setDosareLoaded(true);
  };

  const loadDosarStats = async () => {
    try {
      const res = await authFetch('/api/v1/dosare/stats');
      if (res.ok) setDosarStats(await res.json());
    } catch { /* ignore */ }
  };

  const loadDosarDetail = async (id: string) => {
    try {
      const res = await authFetch(`/api/v1/dosare/${id}`);
      if (res.ok) {
        setDosarViewing(await res.json());
        loadDosarDocuments(id);
      }
    } catch { /* ignore */ }
  };

  const saveDosarForm = async () => {
    const body: any = { ...dosarForm };
    if (!body.valoare_estimata) delete body.valoare_estimata;
    else body.valoare_estimata = parseFloat(body.valoare_estimata);
    // Remove empty strings
    Object.keys(body).forEach(k => { if (body[k] === '') delete body[k]; });
    body.titlu = dosarForm.titlu;

    try {
      const url = dosarEditing ? `/api/v1/dosare/${dosarEditing}` : '/api/v1/dosare/';
      const method = dosarEditing ? 'PUT' : 'POST';
      const res = await authFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (res.ok) {
        setDosarShowForm(false);
        setDosarEditing(null);
        setDosarForm({ titlu: '', descriere: '', client: '', autoritate_contractanta: '', numar_dosar: '', numar_procedura: '', cod_cpv: '', valoare_estimata: '', tip_procedura: '', termen_depunere: '', termen_solutionare: '', note: '' });
        loadDosare();
        loadDosarStats();
        setSaveStatus({ type: 'success', text: dosarEditing ? 'Dosar actualizat' : 'Dosar creat' });
      } else {
        const err = await res.json().catch(() => ({}));
        setSaveStatus({ type: 'error', text: err.detail || 'Eroare la salvare' });
      }
    } catch {
      setSaveStatus({ type: 'error', text: 'Eroare de rețea' });
    }
    setTimeout(() => setSaveStatus(null), 3000);
  };

  const deleteDosarById = async (id: string) => {
    if (!confirm('Ștergeți acest dosar? Artefactele linkuite nu vor fi șterse.')) return;
    try {
      await authFetch(`/api/v1/dosare/${id}`, { method: 'DELETE' });
      if (dosarViewing?.id === id) setDosarViewing(null);
      if (activeDosarId === id) deactivateDosar();
      loadDosare();
      loadDosarStats();
      setSaveStatus({ type: 'success', text: 'Dosar șters' });
    } catch { /* ignore */ }
    setTimeout(() => setSaveStatus(null), 3000);
  };

  const unlinkArtifact = async (dosarId: string, type: string, artifactId: string) => {
    try {
      await authFetch(`/api/v1/dosare/${dosarId}/unlink`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact_type: type, artifact_id: artifactId }),
      });
      loadDosarDetail(dosarId);
    } catch { /* ignore */ }
  };

  // --- Active Dosar Functions ---
  const activateDosar = async (dosarId: string) => {
    try {
      setDosarDocsLoading(true);
      const [infoRes, textsRes] = await Promise.all([
        authFetch(`/api/v1/dosare/${dosarId}`),
        authFetch(`/api/v1/dosare/${dosarId}/documents/texts`),
      ]);
      if (infoRes.ok && textsRes.ok) {
        const info = await infoRes.json();
        const texts = await textsRes.json();
        setActiveDosarId(dosarId);
        setActiveDosarInfo({ titlu: info.titlu, client: info.client, status: info.status });
        setActiveDosarDocs(texts.map((t: any) => ({ id: t.id, filename: t.filename, text: t.extracted_text })));
        localStorage.setItem('activeDosarId', dosarId);
        setSaveStatus({ type: 'success', text: `Dosar activ: ${info.titlu}` });
      }
    } catch { /* ignore */ }
    setDosarDocsLoading(false);
    setTimeout(() => setSaveStatus(null), 3000);
  };

  const deactivateDosar = () => {
    setActiveDosarId(null);
    setActiveDosarInfo(null);
    setActiveDosarDocs([]);
    localStorage.removeItem('activeDosarId');
    setSaveStatus({ type: 'success', text: 'Dosar dezactivat' });
    setTimeout(() => setSaveStatus(null), 3000);
  };

  // Load active dosar on startup
  useEffect(() => {
    const storedId = localStorage.getItem('activeDosarId');
    if (storedId && authState.user && !activeDosarInfo) {
      activateDosar(storedId).catch(() => {
        localStorage.removeItem('activeDosarId');
        setActiveDosarId(null);
      });
    }
  }, [authState.user]);

  // --- Dosar Document Management ---
  const loadDosarDocuments = async (dosarId: string) => {
    try {
      const res = await authFetch(`/api/v1/dosare/${dosarId}/documents`);
      if (res.ok) setDosarDocuments(await res.json());
    } catch { /* ignore */ }
  };

  const uploadDosarDocument = async (dosarId: string, file: File) => {
    setDosarDocUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await authFetch(`/api/v1/dosare/${dosarId}/documents`, { method: 'POST', body: formData });
      if (res.ok) {
        loadDosarDocuments(dosarId);
        if (activeDosarId === dosarId) {
          // Refresh active dosar docs
          const textsRes = await authFetch(`/api/v1/dosare/${dosarId}/documents/texts`);
          if (textsRes.ok) {
            const texts = await textsRes.json();
            setActiveDosarDocs(texts.map((t: any) => ({ id: t.id, filename: t.filename, text: t.extracted_text })));
          }
        }
        setSaveStatus({ type: 'success', text: 'Document încărcat' });
      } else {
        const err = await res.json().catch(() => ({}));
        setSaveStatus({ type: 'error', text: err.detail || 'Eroare la încărcare' });
      }
    } catch { setSaveStatus({ type: 'error', text: 'Eroare la încărcare' }); }
    setDosarDocUploading(false);
    setTimeout(() => setSaveStatus(null), 3000);
  };

  const deleteDosarDocument = async (dosarId: string, docId: string) => {
    try {
      const res = await authFetch(`/api/v1/dosare/${dosarId}/documents/${docId}`, { method: 'DELETE' });
      if (res.ok) {
        loadDosarDocuments(dosarId);
        if (activeDosarId === dosarId) {
          setActiveDosarDocs(prev => prev.filter(d => d.id !== docId));
        }
      }
    } catch { /* ignore */ }
  };

  const DOSAR_STATUS_COLORS: Record<string, string> = {
    activ: 'bg-green-100 text-green-700 border-green-200',
    in_lucru: 'bg-blue-100 text-blue-700 border-blue-200',
    depus: 'bg-purple-100 text-purple-700 border-purple-200',
    finalizat: 'bg-slate-100 text-slate-700 border-slate-200',
    arhivat: 'bg-gray-100 text-gray-500 border-gray-200',
  };
  const DOSAR_STATUS_LABELS: Record<string, string> = {
    activ: 'Activ', in_lucru: 'În lucru', depus: 'Depus', finalizat: 'Finalizat', arhivat: 'Arhivat',
  };

  // --- Active Dosar Banner (shown on tool pages) ---
  const [bannerSelectedDocs, setBannerSelectedDocs] = useState<Set<string>>(new Set());

  // Sync banner selections when active dosar docs change
  useEffect(() => {
    setBannerSelectedDocs(new Set(activeDosarDocs.map(d => d.id)));
  }, [activeDosarDocs]);

  const renderActiveDosarBanner = (onLoadSelected: (docs: {name: string, text: string}[]) => void, multiDocMode?: boolean) => {
    if (!activeDosarId || !activeDosarInfo) return null;

    const handleLoadSelected = () => {
      const selected = activeDosarDocs.filter(d => bannerSelectedDocs.has(d.id));
      if (selected.length === 0) return;
      onLoadSelected(selected.map(d => ({ name: d.filename, text: d.text })));
      setSaveStatus({ type: 'success', text: `${selected.length} documente încărcate din dosar` });
      setTimeout(() => setSaveStatus(null), 3000);
    };

    const toggleDoc = (id: string) => {
      setBannerSelectedDocs(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
      });
    };

    return (
      <div className="mx-4 mt-3 mb-1 p-3 rounded-xl border border-blue-200 bg-blue-50/60">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2 text-sm font-medium text-blue-800">
            <Briefcase size={16} className="text-blue-600" />
            <span>Dosar activ: {activeDosarInfo.titlu}</span>
            <span className="text-xs text-blue-500">— {activeDosarDocs.length} documente</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => { setMode('dosare'); if (activeDosarId) loadDosarDetail(activeDosarId); }} className="text-xs text-blue-600 hover:underline">Schimbă dosar</button>
            <button onClick={deactivateDosar} className="text-xs text-red-500 hover:underline">Dezactivează</button>
          </div>
        </div>
        {activeDosarDocs.length === 0 ? (
          <p className="text-xs text-blue-400 italic">Niciun document atașat la acest dosar. Adăugați documente din pagina dosarului.</p>
        ) : multiDocMode ? (
          <p className="text-xs text-blue-400 italic">Multi-Document acceptă fișiere direct — încărcați-le din zona de upload de mai jos.</p>
        ) : (
          <>
            <div className="space-y-1 mb-2 max-h-32 overflow-y-auto">
              {activeDosarDocs.map(doc => {
                const wordCount = doc.text.split(/\s+/).length;
                return (
                  <label key={doc.id} className="flex items-center gap-2 text-xs cursor-pointer hover:bg-blue-100/50 rounded px-1 py-0.5">
                    <input type="checkbox" checked={bannerSelectedDocs.has(doc.id)} onChange={() => toggleDoc(doc.id)} className="rounded border-blue-300 text-blue-600" />
                    <span className="text-slate-700 truncate flex-1">{doc.filename}</span>
                    <span className="text-blue-400 shrink-0">({wordCount.toLocaleString('ro-RO')} cuv.)</span>
                  </label>
                );
              })}
            </div>
            <button
              onClick={handleLoadSelected}
              disabled={bannerSelectedDocs.size === 0}
              className="text-xs bg-blue-600 text-white px-3 py-1.5 rounded-lg font-medium hover:bg-blue-700 transition disabled:opacity-50 flex items-center gap-1"
            >
              <Download size={12} /> Încarcă {bannerSelectedDocs.size} documente selectate
            </button>
          </>
        )}
      </div>
    );
  };

  const renderDosare = () => {
    // Auto-load (only once)
    if (!dosareLoaded && !dosareLoading && authState.user) {
      loadDosare();
      loadDosarStats();
    }

    if (dosarViewing) {
      const d = dosarViewing;
      const ARTIFACT_LABELS: Record<string, string> = { conversatie: 'Conversație', document: 'Document', red_flags: 'Red Flags', training: 'Material Training' };
      const ARTIFACT_ICONS: Record<string, any> = { conversatie: MessageSquare, document: FileText, red_flags: AlertTriangle, training: GraduationCap };
      return (
        <div className="h-full overflow-y-auto p-6">
        <div className="max-w-5xl mx-auto">
          <button onClick={() => setDosarViewing(null)} className="text-sm text-blue-600 hover:underline mb-4 flex items-center gap-1"><ChevronRight size={14} className="rotate-180" /> Înapoi la dosare</button>
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="p-6 border-b border-slate-100">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2"><Briefcase size={20} className="text-blue-600" /> {d.titlu}</h2>
                  {d.descriere && <p className="text-sm text-slate-500 mt-1">{d.descriere}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`px-3 py-1 rounded-full text-xs font-medium border ${DOSAR_STATUS_COLORS[d.status] || 'bg-slate-100'}`}>{DOSAR_STATUS_LABELS[d.status] || d.status}</span>
                  {activeDosarId === d.id ? (
                    <button onClick={deactivateDosar} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-green-100 text-green-700 border border-green-200 hover:bg-green-200 transition flex items-center gap-1"><CheckCircle size={14} /> Activ</button>
                  ) : (
                    <button onClick={() => activateDosar(d.id)} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-50 text-blue-600 border border-blue-200 hover:bg-blue-100 transition flex items-center gap-1"><Briefcase size={14} /> Setează Activ</button>
                  )}
                  <button onClick={() => { setDosarEditing(d.id); setDosarForm({ titlu: d.titlu || '', descriere: d.descriere || '', client: d.client || '', autoritate_contractanta: d.autoritate_contractanta || '', numar_dosar: d.numar_dosar || '', numar_procedura: d.numar_procedura || '', cod_cpv: d.cod_cpv || '', valoare_estimata: d.valoare_estimata?.toString() || '', tip_procedura: d.tip_procedura || '', termen_depunere: d.termen_depunere?.slice(0, 16) || '', termen_solutionare: d.termen_solutionare?.slice(0, 16) || '', note: d.note || '' }); setDosarShowForm(true); setDosarViewing(null); }} className="p-1.5 text-slate-400 hover:text-blue-600"><Pencil size={16} /></button>
                  <button onClick={() => deleteDosarById(d.id)} className="p-1.5 text-slate-400 hover:text-red-600"><Trash2 size={16} /></button>
                </div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 text-sm">
                {d.client && <div><span className="text-slate-400">Client:</span> <span className="font-medium">{d.client}</span></div>}
                {d.autoritate_contractanta && <div><span className="text-slate-400">AC:</span> <span className="font-medium">{d.autoritate_contractanta}</span></div>}
                {d.numar_dosar && <div><span className="text-slate-400">Nr. dosar:</span> <span className="font-medium">{d.numar_dosar}</span></div>}
                {d.cod_cpv && <div><span className="text-slate-400">CPV:</span> <span className="font-medium">{d.cod_cpv}</span></div>}
                {d.tip_procedura && <div><span className="text-slate-400">Procedură:</span> <span className="font-medium">{d.tip_procedura}</span></div>}
                {d.valoare_estimata && <div><span className="text-slate-400">Valoare:</span> <span className="font-medium">{Number(d.valoare_estimata).toLocaleString('ro-RO')} RON</span></div>}
                {d.termen_depunere && <div><span className="text-slate-400">Termen depunere:</span> <span className="font-medium">{new Date(d.termen_depunere).toLocaleDateString('ro-RO')}</span></div>}
                {d.termen_solutionare && <div><span className="text-slate-400">Termen soluționare:</span> <span className="font-medium">{new Date(d.termen_solutionare).toLocaleDateString('ro-RO')}</span></div>}
              </div>
              {d.note && <div className="mt-3 p-3 bg-yellow-50 rounded-lg text-sm text-yellow-800 border border-yellow-100"><strong>Note:</strong> {d.note}</div>}
            </div>
            <div className="p-6">
              <h3 className="font-semibold text-slate-700 mb-3">Artefacte linkuite ({d.artifacts?.length || 0})</h3>
              {(!d.artifacts || d.artifacts.length === 0) ? (
                <p className="text-sm text-slate-400">Niciun artefact linkuit. Puteți linki conversații, documente, analize Red Flags și materiale de training din paginile respective.</p>
              ) : (
                <div className="space-y-2">
                  {d.artifacts.map((a: any) => {
                    const Icon = ARTIFACT_ICONS[a.tip] || FileText;
                    return (
                      <div key={a.id} className="flex items-center justify-between p-3 rounded-lg border border-slate-100 hover:border-slate-200 transition">
                        <div className="flex items-center gap-3">
                          <Icon size={16} className="text-slate-400" />
                          <div>
                            <p className="text-sm font-medium text-slate-700">{a.titlu}</p>
                            <p className="text-xs text-slate-400">{ARTIFACT_LABELS[a.tip]} · {new Date(a.created_at).toLocaleDateString('ro-RO')}</p>
                          </div>
                        </div>
                        <button onClick={() => unlinkArtifact(d.id, a.tip, a.id)} className="text-xs text-red-500 hover:underline">Delinki</button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            {/* Documente Atașate Section */}
            <div className="p-6 border-t border-slate-100">
              <h3 className="font-semibold text-slate-700 mb-3 flex items-center gap-2">
                <Upload size={16} className="text-blue-500" />
                Documente Atașate ({dosarDocuments.length} / {30})
              </h3>
              <p className="text-xs text-slate-400 mb-3">Încărcați documentele sursă (caiet de sarcini, anunț, ofertă) — textul extras va fi disponibil pe toate paginile de instrumente când dosarul este activ.</p>
              <div className="flex items-center gap-3 mb-3">
                <input
                  type="file"
                  accept=".pdf,.docx,.doc,.txt,.md"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) uploadDosarDocument(d.id, file);
                    e.target.value = '';
                  }}
                  disabled={dosarDocUploading || dosarDocuments.length >= 30}
                  className="block flex-1 text-sm text-slate-600 file:mr-4 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 disabled:opacity-50"
                />
                {dosarDocUploading && <Loader2 className="animate-spin text-blue-500" size={16} />}
              </div>
              {dosarDocuments.length > 0 ? (
                <div className="space-y-2">
                  {dosarDocuments.map((doc: any) => (
                    <div key={doc.id} className="flex items-center justify-between p-3 rounded-lg border border-slate-100 hover:border-slate-200 transition bg-slate-50/50">
                      <div className="flex items-center gap-3 min-w-0">
                        <FileText size={16} className="text-blue-400 shrink-0" />
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-slate-700 truncate">{doc.filename}</p>
                          <p className="text-xs text-slate-400">
                            {doc.text_stats?.words?.toLocaleString('ro-RO') || '?'} cuvinte · {doc.file_size ? `${(doc.file_size / 1024).toFixed(0)} KB` : '?'} · {new Date(doc.created_at).toLocaleDateString('ro-RO')}
                          </p>
                        </div>
                      </div>
                      <button onClick={() => deleteDosarDocument(d.id, doc.id)} className="text-xs text-red-500 hover:underline shrink-0 ml-2">Șterge</button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-slate-400 italic">Niciun document atașat.</p>
              )}
            </div>
          </div>
        </div>
        </div>
      );
    }

    return (
      <div className="h-full overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2"><Briefcase size={24} className="text-blue-600" /> Dosare Digitale</h2>
            <p className="text-sm text-slate-500 mt-1">Gestionează dosarele de contestare — grupează conversații, documente și analize într-un singur loc.</p>
          </div>
          <button onClick={() => { setDosarShowForm(true); setDosarEditing(null); setDosarForm({ titlu: '', descriere: '', client: '', autoritate_contractanta: '', numar_dosar: '', numar_procedura: '', cod_cpv: '', valoare_estimata: '', tip_procedura: '', termen_depunere: '', termen_solutionare: '', note: '' }); }} className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition flex items-center gap-2"><Plus size={16} /> Dosar Nou</button>
        </div>

        {/* Stats */}
        {dosarStats && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
            <button onClick={() => { setDosarFilter(''); loadDosare(); }} className={`p-3 rounded-lg border text-center transition ${!dosarFilter ? 'border-blue-300 bg-blue-50' : 'border-slate-200 hover:border-slate-300'}`}>
              <p className="text-lg font-bold text-slate-800">{dosarStats.total}</p><p className="text-xs text-slate-500">Total</p>
            </button>
            {['activ', 'in_lucru', 'depus', 'finalizat', 'arhivat'].map(s => (
              <button key={s} onClick={() => { setDosarFilter(s); setTimeout(loadDosare, 0); }} className={`p-3 rounded-lg border text-center transition ${dosarFilter === s ? 'border-blue-300 bg-blue-50' : 'border-slate-200 hover:border-slate-300'}`}>
                <p className="text-lg font-bold text-slate-800">{dosarStats.by_status?.[s] || 0}</p><p className="text-xs text-slate-500">{DOSAR_STATUS_LABELS[s]}</p>
              </button>
            ))}
          </div>
        )}

        {/* Create/Edit Form Modal */}
        {dosarShowForm && (
          <div className="mb-6 bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <h3 className="font-semibold text-slate-700 mb-4">{dosarEditing ? 'Editare Dosar' : 'Dosar Nou'}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <label className="text-xs text-slate-500 font-medium">Titlu *</label>
                <input value={dosarForm.titlu} onChange={e => setDosarForm(p => ({ ...p, titlu: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="ex: Contestație procedură achiziție echipamente IT" />
              </div>
              <div className="md:col-span-2">
                <label className="text-xs text-slate-500 font-medium">Descriere</label>
                <textarea value={dosarForm.descriere} onChange={e => setDosarForm(p => ({ ...p, descriere: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" rows={2} placeholder="Descriere scurtă a dosarului" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Client</label>
                <input value={dosarForm.client} onChange={e => setDosarForm(p => ({ ...p, client: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="Numele clientului" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Autoritate Contractantă</label>
                <input value={dosarForm.autoritate_contractanta} onChange={e => setDosarForm(p => ({ ...p, autoritate_contractanta: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="Autoritatea contractantă" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Nr. Dosar</label>
                <input value={dosarForm.numar_dosar} onChange={e => setDosarForm(p => ({ ...p, numar_dosar: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="Referință internă" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Nr. Procedură SEAP</label>
                <input value={dosarForm.numar_procedura} onChange={e => setDosarForm(p => ({ ...p, numar_procedura: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Cod CPV</label>
                <input value={dosarForm.cod_cpv} onChange={e => setDosarForm(p => ({ ...p, cod_cpv: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="ex: 30213000" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Valoare Estimată (RON)</label>
                <input type="number" value={dosarForm.valoare_estimata} onChange={e => setDosarForm(p => ({ ...p, valoare_estimata: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Tip Procedură</label>
                <select value={dosarForm.tip_procedura} onChange={e => setDosarForm(p => ({ ...p, tip_procedura: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1">
                  <option value="">—</option>
                  <option value="licitatie_deschisa">Licitație deschisă</option>
                  <option value="licitatie_restransa">Licitație restrânsă</option>
                  <option value="negociere_competitiva">Negociere competitivă</option>
                  <option value="dialog_competitiv">Dialog competitiv</option>
                  <option value="procedura_simplificata">Procedură simplificată</option>
                  <option value="concurs_solutii">Concurs de soluții</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Termen Depunere</label>
                <input type="datetime-local" value={dosarForm.termen_depunere} onChange={e => setDosarForm(p => ({ ...p, termen_depunere: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Termen Soluționare</label>
                <input type="datetime-local" value={dosarForm.termen_solutionare} onChange={e => setDosarForm(p => ({ ...p, termen_solutionare: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" />
              </div>
              <div className="md:col-span-2">
                <label className="text-xs text-slate-500 font-medium">Note</label>
                <textarea value={dosarForm.note} onChange={e => setDosarForm(p => ({ ...p, note: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" rows={2} />
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-4">
              <button onClick={() => { setDosarShowForm(false); setDosarEditing(null); }} className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800">Anulează</button>
              <button onClick={saveDosarForm} disabled={!dosarForm.titlu.trim()} className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition disabled:opacity-50">{dosarEditing ? 'Actualizează' : 'Creează Dosar'}</button>
            </div>
          </div>
        )}

        {/* Dosare List */}
        {dosareLoading ? (
          <div className="text-center py-12"><Loader2 className="animate-spin mx-auto text-blue-500" size={32} /></div>
        ) : dosare.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl border border-slate-200">
            <Briefcase size={48} className="mx-auto text-slate-300 mb-4" />
            <p className="text-slate-500 text-lg font-medium">Niciun dosar</p>
            <p className="text-slate-400 text-sm mt-1">Creați primul dosar digital pentru a organiza toate artefactele unei contestări.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {dosare.map((d: any) => {
              const totalArtifacts = (d.artifact_counts?.conversatii || 0) + (d.artifact_counts?.documente || 0) + (d.artifact_counts?.red_flags || 0) + (d.artifact_counts?.training_materials || 0);
              const isUrgent = d.termen_depunere && new Date(d.termen_depunere) > new Date() && (new Date(d.termen_depunere).getTime() - Date.now()) < 3 * 24 * 60 * 60 * 1000;
              return (
                <div key={d.id} className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition p-4 cursor-pointer" onClick={() => loadDosarDetail(d.id)}>
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="font-semibold text-slate-800 truncate">{d.titlu}</h3>
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${DOSAR_STATUS_COLORS[d.status] || 'bg-slate-100'}`}>{DOSAR_STATUS_LABELS[d.status] || d.status}</span>
                        {activeDosarId === d.id && <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-100 text-green-700 border border-green-200 flex items-center gap-0.5"><CheckCircle size={10} /> Activ</span>}
                        {isUrgent && <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-red-100 text-red-700 border border-red-200 animate-pulse">Urgent</span>}
                      </div>
                      <div className="flex items-center gap-4 text-xs text-slate-400">
                        {d.client && <span>{d.client}</span>}
                        {d.cod_cpv && <span>CPV: {d.cod_cpv}</span>}
                        {d.numar_dosar && <span>#{d.numar_dosar}</span>}
                        <span>{new Date(d.created_at).toLocaleDateString('ro-RO')}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 ml-4 shrink-0">
                      <div className="text-center" title={`${d.artifact_counts?.conversatii || 0} conversații, ${d.artifact_counts?.documente || 0} documente, ${d.artifact_counts?.red_flags || 0} red flags, ${d.artifact_counts?.training_materials || 0} training`}>
                        <p className="text-sm font-bold text-slate-600">{totalArtifacts}</p>
                        <p className="text-[10px] text-slate-400">artefacte</p>
                      </div>
                      <button onClick={e => { e.stopPropagation(); deleteDosarById(d.id); }} className="p-1.5 text-slate-300 hover:text-red-500 transition"><Trash2 size={14} /></button>
                    </div>
                  </div>
                  {d.termen_depunere && (
                    <div className="mt-2 flex items-center gap-1.5 text-xs text-slate-400">
                      <Clock size={12} /> Termen: {new Date(d.termen_depunere).toLocaleDateString('ro-RO', { day: '2-digit', month: 'short', year: 'numeric' })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      </div>
    );
  };

  // --- Alerte Decizii ---
  const loadAlertRules = async () => {
    setAlertsLoading(true);
    try {
      const res = await authFetch('/api/v1/alerts/');
      if (res.ok) setAlertRules(await res.json());
    } catch { /* ignore */ }
    setAlertsLoading(false);
    setAlertsLoaded(true);
  };

  const saveAlertForm = async () => {
    const filters: any = {};
    if (alertForm.cod_cpv.trim()) filters.cod_cpv = alertForm.cod_cpv.split(',').map((s: string) => s.trim()).filter(Boolean);
    if (alertForm.coduri_critici.trim()) filters.coduri_critici = alertForm.coduri_critici.split(',').map((s: string) => s.trim()).filter(Boolean);
    if (alertForm.complet.trim()) filters.complet = alertForm.complet.split(',').map((s: string) => s.trim()).filter(Boolean);
    if (alertForm.tip_procedura.trim()) filters.tip_procedura = alertForm.tip_procedura.split(',').map((s: string) => s.trim()).filter(Boolean);
    if (alertForm.solutie.trim()) filters.solutie = alertForm.solutie.split(',').map((s: string) => s.trim()).filter(Boolean);
    if (alertForm.keywords.trim()) filters.keywords = alertForm.keywords.split(',').map((s: string) => s.trim()).filter(Boolean);

    const body: any = { nume: alertForm.nume, descriere: alertForm.descriere || undefined, filters, frecventa: alertForm.frecventa };

    try {
      const url = alertEditing ? `/api/v1/alerts/${alertEditing}` : '/api/v1/alerts/';
      const method = alertEditing ? 'PUT' : 'POST';
      const res = await authFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (res.ok) {
        setAlertShowForm(false);
        setAlertEditing(null);
        setAlertForm({ nume: '', descriere: '', cod_cpv: '', coduri_critici: '', complet: '', tip_procedura: '', solutie: '', keywords: '', frecventa: 'zilnic' });
        loadAlertRules();
        setSaveStatus({ type: 'success', text: alertEditing ? 'Regulă actualizată' : 'Regulă creată' });
      } else {
        const err = await res.json().catch(() => ({}));
        setSaveStatus({ type: 'error', text: err.detail || 'Eroare la salvare' });
      }
    } catch {
      setSaveStatus({ type: 'error', text: 'Eroare de rețea' });
    }
    setTimeout(() => setSaveStatus(null), 3000);
  };

  const toggleAlertRule = async (id: string) => {
    try {
      await authFetch(`/api/v1/alerts/${id}/toggle`, { method: 'POST' });
      loadAlertRules();
    } catch { /* ignore */ }
  };

  const deleteAlertRule = async (id: string) => {
    if (!confirm('Ștergeți această regulă de alertă?')) return;
    try {
      await authFetch(`/api/v1/alerts/${id}`, { method: 'DELETE' });
      loadAlertRules();
      setSaveStatus({ type: 'success', text: 'Regulă ștearsă' });
    } catch { /* ignore */ }
    setTimeout(() => setSaveStatus(null), 3000);
  };

  const renderAlerts = () => {
    // Auto-load (only once)
    if (!alertsLoaded && !alertsLoading && authState.user) {
      loadAlertRules();
    }

    return (
      <div className="h-full overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2"><Bell size={24} className="text-amber-500" /> Alerte Decizii Noi</h2>
            <p className="text-sm text-slate-500 mt-1">Primește notificări când apar decizii CNSC noi care corespund criteriilor tale.</p>
          </div>
          <button onClick={() => { setAlertShowForm(true); setAlertEditing(null); setAlertForm({ nume: '', descriere: '', cod_cpv: '', coduri_critici: '', complet: '', tip_procedura: '', solutie: '', keywords: '', frecventa: 'zilnic' }); }} className="bg-amber-500 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-amber-600 transition flex items-center gap-2"><Plus size={16} /> Regulă Nouă</button>
        </div>

        {/* Create/Edit Form */}
        {alertShowForm && (
          <div className="mb-6 bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <h3 className="font-semibold text-slate-700 mb-4">{alertEditing ? 'Editare Regulă' : 'Regulă Nouă de Alertă'}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <label className="text-xs text-slate-500 font-medium">Numele regulii *</label>
                <input value={alertForm.nume} onChange={e => setAlertForm(p => ({ ...p, nume: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="ex: Decizii ADMIS pe CPV 30213000" />
              </div>
              <div className="md:col-span-2">
                <label className="text-xs text-slate-500 font-medium">Descriere</label>
                <input value={alertForm.descriere} onChange={e => setAlertForm(p => ({ ...p, descriere: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="Descriere opțională" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Coduri CPV (separate prin virgulă)</label>
                <input value={alertForm.cod_cpv} onChange={e => setAlertForm(p => ({ ...p, cod_cpv: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="30213000, 48000000" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Coduri Critici</label>
                <input value={alertForm.coduri_critici} onChange={e => setAlertForm(p => ({ ...p, coduri_critici: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="D01, R03" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Complet CNSC</label>
                <input value={alertForm.complet} onChange={e => setAlertForm(p => ({ ...p, complet: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="C1, C5, C10" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Soluție</label>
                <input value={alertForm.solutie} onChange={e => setAlertForm(p => ({ ...p, solutie: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="ADMIS, RESPINS" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Cuvinte cheie</label>
                <input value={alertForm.keywords} onChange={e => setAlertForm(p => ({ ...p, keywords: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1" placeholder="evaluare, preț neobișnuit de scăzut" />
              </div>
              <div>
                <label className="text-xs text-slate-500 font-medium">Frecvență</label>
                <select value={alertForm.frecventa} onChange={e => setAlertForm(p => ({ ...p, frecventa: e.target.value }))} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm mt-1">
                  <option value="zilnic">Zilnic</option>
                  <option value="saptamanal">Săptămânal</option>
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-4">
              <button onClick={() => { setAlertShowForm(false); setAlertEditing(null); }} className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800">Anulează</button>
              <button onClick={saveAlertForm} disabled={!alertForm.nume.trim()} className="bg-amber-500 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-amber-600 transition disabled:opacity-50">{alertEditing ? 'Actualizează' : 'Creează Regulă'}</button>
            </div>
          </div>
        )}

        {/* Rules List */}
        {alertsLoading ? (
          <div className="text-center py-12"><Loader2 className="animate-spin mx-auto text-amber-500" size={32} /></div>
        ) : alertRules.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl border border-slate-200">
            <Bell size={48} className="mx-auto text-slate-300 mb-4" />
            <p className="text-slate-500 text-lg font-medium">Nicio regulă de alertă</p>
            <p className="text-slate-400 text-sm mt-1">Creați o regulă pentru a fi notificat când apar decizii CNSC noi relevante.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {alertRules.map((r: any) => {
              const filterLabels: string[] = [];
              if (r.filters?.cod_cpv?.length) filterLabels.push(`CPV: ${r.filters.cod_cpv.join(', ')}`);
              if (r.filters?.coduri_critici?.length) filterLabels.push(`Critici: ${r.filters.coduri_critici.join(', ')}`);
              if (r.filters?.complet?.length) filterLabels.push(`Complet: ${r.filters.complet.join(', ')}`);
              if (r.filters?.solutie?.length) filterLabels.push(`Soluție: ${r.filters.solutie.join(', ')}`);
              if (r.filters?.keywords?.length) filterLabels.push(`Keywords: ${r.filters.keywords.join(', ')}`);
              if (r.filters?.tip_procedura?.length) filterLabels.push(`Procedură: ${r.filters.tip_procedura.join(', ')}`);

              return (
                <div key={r.id} className={`bg-white rounded-xl border shadow-sm p-4 transition ${r.activ ? 'border-slate-200' : 'border-slate-100 opacity-60'}`}>
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="font-semibold text-slate-800">{r.nume}</h3>
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${r.activ ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-500'}`}>{r.activ ? 'Activ' : 'Inactiv'}</span>
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500">{r.frecventa === 'zilnic' ? 'Zilnic' : 'Săptămânal'}</span>
                      </div>
                      {r.descriere && <p className="text-xs text-slate-400 mb-2">{r.descriere}</p>}
                      <div className="flex flex-wrap gap-1.5">
                        {filterLabels.map((label, i) => (
                          <span key={i} className="text-[10px] bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full border border-amber-100">{label}</span>
                        ))}
                      </div>
                      <div className="mt-2 text-xs text-slate-400">
                        {r.total_notificari} notificări trimise{r.ultima_notificare && ` · Ultima: ${new Date(r.ultima_notificare).toLocaleDateString('ro-RO')}`}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 ml-4 shrink-0">
                      <button onClick={() => toggleAlertRule(r.id)} className={`p-1.5 rounded-lg transition ${r.activ ? 'text-green-600 hover:bg-green-50' : 'text-slate-400 hover:bg-slate-50'}`} title={r.activ ? 'Dezactivează' : 'Activează'}>
                        {r.activ ? <CheckCircle size={18} /> : <XCircle size={18} />}
                      </button>
                      <button onClick={() => { setAlertEditing(r.id); setAlertForm({ nume: r.nume, descriere: r.descriere || '', cod_cpv: r.filters?.cod_cpv?.join(', ') || '', coduri_critici: r.filters?.coduri_critici?.join(', ') || '', complet: r.filters?.complet?.join(', ') || '', tip_procedura: r.filters?.tip_procedura?.join(', ') || '', solutie: r.filters?.solutie?.join(', ') || '', keywords: r.filters?.keywords?.join(', ') || '', frecventa: r.frecventa }); setAlertShowForm(true); }} className="p-1.5 text-slate-400 hover:text-blue-600 transition"><Pencil size={16} /></button>
                      <button onClick={() => deleteAlertRule(r.id)} className="p-1.5 text-slate-400 hover:text-red-600 transition"><Trash2 size={16} /></button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-8 p-4 bg-amber-50 rounded-xl border border-amber-100 text-sm text-amber-800">
          <p className="font-medium mb-1">Cum funcționează alertele?</p>
          <p className="text-xs text-amber-600">Când pipeline-ul zilnic de import aduce decizii noi, acestea sunt verificate automat contra regulilor active. Dacă o decizie se potrivește, veți primi un email cu detaliile deciziei. Verificați că adresa de email este confirmată în Profil.</p>
        </div>
      </div>
      </div>
    );
  };

  const renderProfile = () => {
    const user = authState.user;
    if (!user) return null;

    const handleUpdateName = async () => {
      if (!profileNume.trim() || profileNume === user.nume) return;
      setProfileLoading(true);
      setProfileMessage(null);
      try {
        const res = await authFetch('/api/v1/auth/me', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ nume: profileNume.trim() }),
        });
        if (res.ok) {
          const data = await res.json();
          setAuthState(prev => ({ ...prev, user: data }));
          setProfileMessage({ type: 'success', text: 'Numele a fost actualizat' });
        } else {
          const err = await res.json();
          setProfileMessage({ type: 'error', text: err.detail || 'Eroare' });
        }
      } catch { setProfileMessage({ type: 'error', text: 'Eroare de rețea' }); }
      setProfileLoading(false);
    };

    const handleChangePassword = async () => {
      if (!profileCurrentPassword || !profileNewPassword) return;
      if (profileNewPassword !== profileConfirmPassword) {
        setProfileMessage({ type: 'error', text: 'Parolele noi nu coincid' });
        return;
      }
      if (profileNewPassword.length < 8) {
        setProfileMessage({ type: 'error', text: 'Parola nouă trebuie să aibă minim 8 caractere' });
        return;
      }
      setProfileLoading(true);
      setProfileMessage(null);
      try {
        const res = await authFetch('/api/v1/auth/change-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: profileCurrentPassword, new_password: profileNewPassword }),
        });
        if (res.ok) {
          setProfileMessage({ type: 'success', text: 'Parola a fost schimbată cu succes' });
          setProfileCurrentPassword(''); setProfileNewPassword(''); setProfileConfirmPassword('');
        } else {
          const err = await res.json();
          setProfileMessage({ type: 'error', text: err.detail || 'Eroare' });
        }
      } catch { setProfileMessage({ type: 'error', text: 'Eroare de rețea' }); }
      setProfileLoading(false);
    };

    const handleVerifyEmail = async () => {
      if (!verificationCode || verificationCode.length !== 6) {
        setVerificationMessage({ type: 'error', text: 'Introdu codul de 6 cifre' });
        return;
      }
      setVerificationLoading(true);
      setVerificationMessage(null);
      try {
        const res = await authFetch('/api/v1/auth/verify-email', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: verificationCode }),
        });
        if (res.ok) {
          setVerificationMessage({ type: 'success', text: 'Email verificat cu succes!' });
          setVerificationCode('');
          refreshAuthUser();
        } else {
          const err = await res.json();
          setVerificationMessage({ type: 'error', text: err.detail || 'Eroare' });
        }
      } catch { setVerificationMessage({ type: 'error', text: 'Eroare de rețea' }); }
      setVerificationLoading(false);
    };

    const handleResendCode = async () => {
      setVerificationLoading(true);
      setVerificationMessage(null);
      try {
        const res = await authFetch('/api/v1/auth/resend-verification', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
        if (res.ok) {
          setVerificationMessage({ type: 'success', text: 'Cod trimis pe email' });
        } else {
          const err = await res.json();
          setVerificationMessage({ type: 'error', text: err.detail || 'Eroare' });
        }
      } catch { setVerificationMessage({ type: 'error', text: 'Eroare de rețea' }); }
      setVerificationLoading(false);
    };

    return (
      <div className="h-full overflow-y-auto bg-slate-50/50 p-4 md:p-8">
        <div className="max-w-2xl mx-auto">
          <h2 className="text-xl md:text-2xl font-bold text-slate-800 mb-1 flex items-center gap-3">
            <UserCircle className="text-blue-500" size={24} /> Profil utilizator
          </h2>
          <p className="text-sm text-slate-500 mb-6 md:mb-8">Gestionează contul și setările de securitate.</p>

          {/* Account Info */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6 shadow-sm">
            <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-4">Informații cont</h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between py-2 border-b border-slate-100">
                <span className="text-sm text-slate-500">Email</span>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-800">{user.email}</span>
                  {user.email_verified ? (
                    <span className="text-[10px] text-green-600 bg-green-50 px-1.5 py-0.5 rounded-full font-medium flex items-center gap-1"><CheckCircle size={10}/> Verificat</span>
                  ) : (
                    <span className="text-[10px] text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full font-medium">Neverificat</span>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between py-2 border-b border-slate-100">
                <span className="text-sm text-slate-500">Plan curent</span>
                <span className="text-sm font-medium text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full">{PLAN_LABELS[user.rol] || user.rol}</span>
              </div>
              <div className="flex items-center justify-between py-2 border-b border-slate-100">
                <span className="text-sm text-slate-500">Interogări azi</span>
                <span className="text-sm font-medium text-slate-800">{user.queries_today} / {user.queries_limit}</span>
              </div>
              {user.created_at && (
                <div className="flex items-center justify-between py-2">
                  <span className="text-sm text-slate-500">Membru din</span>
                  <span className="text-sm font-medium text-slate-800">{new Date(user.created_at).toLocaleDateString('ro-RO', { year: 'numeric', month: 'long', day: 'numeric' })}</span>
                </div>
              )}
            </div>
            {user.rol !== 'admin' && (
              <button onClick={() => { setMode('pricing'); }} className="mt-4 w-full text-sm text-blue-600 bg-blue-50 hover:bg-blue-100 py-2 rounded-lg font-medium transition-colors">
                Upgrade plan
              </button>
            )}
          </div>

          {/* Email Verification */}
          {!user.email_verified && (
            <div className="bg-yellow-50 rounded-xl border border-yellow-200 p-6 mb-6 shadow-sm">
              <h3 className="text-sm font-bold text-yellow-700 uppercase tracking-wider mb-2 flex items-center gap-2">
                <Shield size={16} /> Verificare email
              </h3>
              <p className="text-sm text-yellow-600 mb-4">Introdu codul de 6 cifre trimis pe adresa <strong>{user.email}</strong></p>
              <div className="flex gap-3">
                <input
                  type="text"
                  value={verificationCode}
                  onChange={e => setVerificationCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  placeholder="000000"
                  className="flex-1 p-3 rounded-lg bg-white border border-yellow-300 text-center text-lg font-mono tracking-[0.5em] focus:ring-2 focus:ring-yellow-500 outline-none"
                  maxLength={6}
                  onKeyDown={e => e.key === 'Enter' && handleVerifyEmail()}
                />
                <button onClick={handleVerifyEmail} disabled={verificationLoading} className="px-6 bg-yellow-600 text-white rounded-lg font-medium hover:bg-yellow-700 disabled:opacity-50 transition-colors">
                  {verificationLoading ? 'Se verifică...' : 'Verifică'}
                </button>
              </div>
              <button onClick={handleResendCode} disabled={verificationLoading} className="mt-3 text-sm text-yellow-600 hover:text-yellow-800 underline transition-colors">
                Retrimite codul
              </button>
              {verificationMessage && (
                <div className={`mt-3 p-3 rounded-lg text-sm font-medium ${verificationMessage.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
                  {verificationMessage.text}
                </div>
              )}
            </div>
          )}

          {/* Edit Name */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6 shadow-sm">
            <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-4">Editează numele</h3>
            <input
              type="text"
              value={profileNume}
              onChange={e => setProfileNume(e.target.value)}
              placeholder={user.nume || 'Introdu numele tău'}
              className="w-full p-3 rounded-lg bg-slate-50 border border-slate-300 text-sm focus:ring-2 focus:ring-blue-500 outline-none mb-3"
            />
            <button onClick={handleUpdateName} disabled={profileLoading || !profileNume.trim()} className="w-full py-2.5 rounded-lg bg-blue-600 text-white font-medium text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors">
              {profileLoading ? 'Se salvează...' : 'Salvează numele'}
            </button>
          </div>

          {/* Change Password */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6 shadow-sm">
            <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-4 flex items-center gap-2">
              <Lock size={16} /> Schimbă parola
            </h3>
            <div className="space-y-3">
              <input
                type="password"
                value={profileCurrentPassword}
                onChange={e => setProfileCurrentPassword(e.target.value)}
                placeholder="Parola curentă"
                className="w-full p-3 rounded-lg bg-slate-50 border border-slate-300 text-sm focus:ring-2 focus:ring-blue-500 outline-none"
              />
              <input
                type="password"
                value={profileNewPassword}
                onChange={e => setProfileNewPassword(e.target.value)}
                placeholder="Parola nouă (minim 8 caractere)"
                className="w-full p-3 rounded-lg bg-slate-50 border border-slate-300 text-sm focus:ring-2 focus:ring-blue-500 outline-none"
              />
              <input
                type="password"
                value={profileConfirmPassword}
                onChange={e => setProfileConfirmPassword(e.target.value)}
                placeholder="Confirmă parola nouă"
                className="w-full p-3 rounded-lg bg-slate-50 border border-slate-300 text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                onKeyDown={e => e.key === 'Enter' && handleChangePassword()}
              />
            </div>
            <button onClick={handleChangePassword} disabled={profileLoading || !profileCurrentPassword || !profileNewPassword} className="w-full mt-4 py-2.5 rounded-lg bg-slate-800 text-white font-medium text-sm hover:bg-slate-900 disabled:opacity-50 transition-colors">
              {profileLoading ? 'Se schimbă...' : 'Schimbă parola'}
            </button>
          </div>

          {/* Persistent Memory Section */}
          <div className="bg-white border border-slate-200 rounded-xl p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-slate-700 flex items-center gap-2">
                <Database size={16} className="text-blue-500" /> Memorie Persistentă AI
              </h3>
              <button onClick={async () => {
                if (!confirm('Ștergi toată memoria? AI-ul nu va mai reține informațiile despre tine.')) return;
                await authFetch('/api/v1/chat/memory', { method: 'DELETE' });
                setProfileMessage({ type: 'success', text: 'Memoria a fost ștearsă' });
              }} className="text-xs text-red-600 hover:text-red-800 font-medium">Șterge tot</button>
            </div>
            <p className="text-xs text-slate-500 mb-3">AI-ul reține automat informații utile din conversațiile tale (domeniu activitate, cazuri în curs, preferințe).</p>
            <MemoryList />
          </div>

          {/* Messages */}
          {profileMessage && (
            <div className={`p-4 rounded-lg mb-4 text-sm font-medium ${profileMessage.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
              {profileMessage.text}
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderPricing = () => {
    const plans = [
      {
        id: 'registered',
        name: 'Free',
        price: 'Gratuit',
        features: ['Chat AI (5 interogări/zi)', 'Dashboard', 'Data Lake', 'Jurisprudență RAG'],
        color: 'slate',
      },
      {
        id: 'paid_basic',
        name: 'Basic',
        price: 'Contactează-ne',
        features: ['Tot ce include Free', 'Drafter Contestații', 'Red Flags Detector', 'Clarificări', '20 interogări/zi'],
        color: 'blue',
        popular: true,
      },
      {
        id: 'paid_pro',
        name: 'Pro',
        price: 'Contactează-ne',
        features: ['Tot ce include Basic', 'TrainingAP', 'Export materiale', '100 interogări/zi'],
        color: 'purple',
      },
      {
        id: 'paid_enterprise',
        name: 'Enterprise',
        price: 'Contactează-ne',
        features: ['Tot ce include Pro', 'Acces API', 'Interogări nelimitate', 'Suport dedicat'],
        color: 'amber',
      },
    ];

    const currentPlan = authState.user?.rol || 'registered';

    return (
      <div className="h-full overflow-y-auto bg-slate-50/50 p-4 md:p-8">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-8">
            <h2 className="text-xl md:text-2xl font-bold text-slate-800 mb-2">Planuri și prețuri</h2>
            <p className="text-sm text-slate-500">Alege planul potrivit pentru nevoile tale în achiziții publice.</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {plans.map(plan => {
              const isCurrent = currentPlan === plan.id;
              const borderColor = plan.popular ? 'border-blue-500 ring-2 ring-blue-200' : 'border-slate-200';
              return (
                <div key={plan.id} className={`bg-white rounded-xl border-2 ${isCurrent ? 'border-green-500 ring-2 ring-green-200' : borderColor} p-6 shadow-sm flex flex-col relative`}>
                  {plan.popular && !isCurrent && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-blue-600 text-white text-[10px] font-bold px-3 py-1 rounded-full uppercase tracking-wider">Popular</div>
                  )}
                  {isCurrent && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-green-600 text-white text-[10px] font-bold px-3 py-1 rounded-full uppercase tracking-wider">Planul tău</div>
                  )}
                  <h3 className="text-lg font-bold text-slate-800 mb-1">{plan.name}</h3>
                  <p className="text-2xl font-bold text-slate-900 mb-4">{plan.price}</p>
                  <ul className="space-y-2 flex-1 mb-6">
                    {plan.features.map((f, i) => (
                      <li key={i} className="text-sm text-slate-600 flex items-start gap-2">
                        <CheckCircle size={14} className="text-green-500 shrink-0 mt-0.5" />
                        {f}
                      </li>
                    ))}
                  </ul>
                  {isCurrent ? (
                    <div className="w-full py-2.5 rounded-lg bg-green-50 text-green-700 font-medium text-sm text-center border border-green-200">
                      Plan activ
                    </div>
                  ) : plan.id === 'registered' ? (
                    <div className="w-full py-2.5 rounded-lg bg-slate-50 text-slate-400 font-medium text-sm text-center border border-slate-200">
                      Plan gratuit
                    </div>
                  ) : (
                    <a href="mailto:contact@expertap.ro?subject=Upgrade%20plan%20ExpertAP%20-%20{plan.name}" className="w-full py-2.5 rounded-lg bg-blue-600 text-white font-medium text-sm text-center hover:bg-blue-700 transition-colors block">
                      Solicită upgrade
                    </a>
                  )}
                </div>
              );
            })}
          </div>

          <div className="mt-8 bg-white rounded-xl border border-slate-200 p-6 shadow-sm text-center">
            <h3 className="text-sm font-bold text-slate-600 mb-2">Ai nevoie de un plan personalizat?</h3>
            <p className="text-sm text-slate-500 mb-4">Contactează-ne pentru oferte speciale pentru firme de avocatură sau echipe mari.</p>
            <a href="mailto:contact@expertap.ro?subject=Plan%20personalizat%20ExpertAP" className="inline-flex items-center gap-2 text-sm text-blue-600 font-medium hover:text-blue-800 transition-colors">
              contact@expertap.ro
            </a>
          </div>
        </div>
      </div>
    );
  };

  const renderSettings = () => {
    const providerLabels: Record<string, string> = { gemini: 'Google Gemini', anthropic: 'Anthropic Claude', openai: 'OpenAI', groq: 'Groq', openrouter: 'OpenRouter' };
    const providerColors: Record<string, string> = { gemini: 'blue', anthropic: 'orange', openai: 'green', groq: 'purple', openrouter: 'rose' };
    const models = llmSettings?.providers?.[settingsProvider]?.models || [];
    const keyFieldNames: Record<string, string> = { gemini: 'GEMINI_API_KEY', anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', groq: 'GROQ_API_KEY', openrouter: 'OPENROUTER_API_KEY' };

    return (
      <div className="h-full overflow-y-auto bg-slate-50/50 p-4 md:p-8">
        <div className="max-w-2xl mx-auto">
          <h2 className="text-xl md:text-2xl font-bold text-slate-800 mb-1 flex items-center gap-3">
            <Settings className="text-blue-500" size={24} /> Setări Model LLM
          </h2>
          <p className="text-sm text-slate-500 mb-6 md:mb-8">Configurează providerul și modelul de limbaj utilizat de ExpertAP.</p>

          {/* Provider Selection */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6 shadow-sm">
            <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-4">Provider activ</h3>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
              {Object.entries(providerLabels).map(([key, label]) => {
                const isActive = settingsProvider === key;
                const isConfigured = llmSettings?.providers?.[key]?.configured;
                const color = providerColors[key];
                return (
                  <button
                    key={key}
                    onClick={() => {
                      setSettingsProvider(key);
                      const providerModels = llmSettings?.providers?.[key]?.models || [];
                      setSettingsModel(providerModels[0]?.id || '');
                      setSettingsTestResult(null);
                      setSettingsMessage(null);
                    }}
                    className={`p-4 rounded-lg border-2 transition-all text-left ${
                      isActive
                        ? `border-${color}-500 bg-${color}-50 ring-2 ring-${color}-200`
                        : 'border-slate-200 hover:border-slate-300 bg-white'
                    }`}
                  >
                    <div className="mb-1">
                      <span className={`text-sm font-bold ${isActive ? `text-${color}-700` : 'text-slate-700'}`}>{label}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-400">{(llmSettings?.providers?.[key]?.models || []).length} modele</span>
                      {isConfigured && <span className="text-[10px] text-green-600 bg-green-50 px-1 py-0.5 rounded whitespace-nowrap">✓</span>}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Model Selection */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6 shadow-sm">
            <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-4">Model</h3>
            <select
              value={settingsModel}
              onChange={(e) => setSettingsModel(e.target.value)}
              className="w-full border border-slate-300 rounded-lg px-4 py-3 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white font-mono"
            >
              <option value="">Implicit ({models[0]?.id || 'auto'})</option>
              {models.map((m: LLMModelInfo) => {
                const fmtTokens = (n: number) => n >= 1_000_000 ? `${(n/1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n/1_000).toFixed(0)}k` : `${n}`;
                const label = m.input_tokens > 0
                  ? `${m.id}  ⟨In: ${fmtTokens(m.input_tokens)} / Out: ${fmtTokens(m.output_tokens)}⟩`
                  : m.id;
                return <option key={m.id} value={m.id}>{label}</option>;
              })}
            </select>
          </div>

          {/* API Key */}
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6 shadow-sm">
            <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-4">Cheie API — {providerLabels[settingsProvider]}</h3>
            <div className="flex items-center gap-2 mb-2">
              {llmSettings?.providers?.[settingsProvider]?.configured ? (
                <span className="text-xs text-green-600 bg-green-50 px-2 py-1 rounded-full font-medium">Cheie configurată</span>
              ) : (
                <span className="text-xs text-yellow-600 bg-yellow-50 px-2 py-1 rounded-full font-medium">Cheie lipsă</span>
              )}
              <span className="text-xs text-slate-400">Env: {keyFieldNames[settingsProvider]}</span>
            </div>
            <input
              type="password"
              value={settingsApiKey}
              onChange={(e) => setSettingsApiKey(e.target.value)}
              placeholder={llmSettings?.providers?.[settingsProvider]?.configured ? 'Lasă gol pentru a păstra cheia existentă' : 'Introdu cheia API...'}
              className="w-full border border-slate-300 rounded-lg px-4 py-3 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
            <p className="text-xs text-slate-400 mt-2">Cheia este criptată înainte de stocare. Poți seta și din variabila de mediu.</p>
          </div>

          {/* Actions */}
          <div className="flex gap-3 mb-6">
            <button
              onClick={handleSaveSettings}
              disabled={settingsSaving}
              className="flex-1 bg-blue-600 text-white py-3 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {settingsSaving ? 'Se salvează...' : 'Salvează setările'}
            </button>
            <button
              onClick={handleTestConnection}
              disabled={settingsTesting}
              className="flex-1 border border-slate-300 text-slate-700 py-3 rounded-lg font-medium hover:bg-slate-50 disabled:opacity-50 transition-colors"
            >
              {settingsTesting ? 'Se testează...' : 'Testează conexiunea'}
            </button>
          </div>

          {/* Messages */}
          {settingsMessage && (
            <div className={`p-4 rounded-lg mb-4 text-sm font-medium ${
              settingsMessage.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'
            }`}>
              {settingsMessage.text}
            </div>
          )}

          {settingsTestResult && (
            <div className={`p-4 rounded-lg mb-4 border ${
              settingsTestResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'
            }`}>
              <div className="flex items-center gap-2 mb-1">
                <span className={`text-sm font-bold ${settingsTestResult.success ? 'text-green-700' : 'text-red-700'}`}>
                  {settingsTestResult.success ? 'Conexiune reușită' : 'Conexiune eșuată'}
                </span>
              </div>
              {settingsTestResult.success && (
                <p className="text-xs text-green-600">Timp de răspuns: {settingsTestResult.response_time_ms}ms</p>
              )}
              {settingsTestResult.error && (
                <p className="text-xs text-red-600 mt-1">{settingsTestResult.error}</p>
              )}
            </div>
          )}

          {/* Info note */}
          <div className="bg-slate-100 rounded-lg p-4 text-xs text-slate-500">
            <p className="font-medium text-slate-600 mb-1">Notă importantă:</p>
            <p>Embedding-urile rămân întotdeauna pe Gemini, indiferent de providerul ales pentru chat/generare. Schimbarea modelului de embedding ar necesita regenerarea tuturor vectorilor din baza de date.</p>
          </div>
        </div>
      </div>
    );
  };

  const renderSpeteANAP = () => {
    return (
      <div className="h-full flex flex-col p-4 md:p-6 overflow-auto">
        {/* Header */}
        <header className="mb-4 md:mb-6 shrink-0">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-4">
            <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2">
              <Layers className="text-teal-600" size={22} /> Spețe ANAP
            </h2>
            {speteStats && (
              <div className="flex gap-2 flex-wrap">
                <span className="text-xs bg-teal-50 text-teal-700 px-3 py-1.5 rounded-lg border border-teal-200 font-medium">
                  Total: {speteStats.total}
                </span>
                <span className="text-xs bg-slate-50 text-slate-600 px-3 py-1.5 rounded-lg border border-slate-200 font-medium">
                  Categorii: {speteStats.categories}
                </span>
              </div>
            )}
          </div>

          {/* Search bar */}
          <div className="flex flex-col sm:flex-row gap-2 mb-4">
            <div className="flex-1 relative">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                className="w-full border border-slate-300 rounded-lg pl-9 pr-3 py-2.5 text-sm focus:ring-2 focus:ring-teal-500 outline-none"
                placeholder="Caută în spețe (întrebare, răspuns)..."
                value={speteSearch}
                onChange={(e) => setSpeteSearch(e.target.value)}
              />
            </div>
            <button
              onClick={() => setSpeteSemantic(!speteSemantic)}
              className={`px-3 py-2 rounded-lg text-xs font-medium border transition whitespace-nowrap ${
                speteSemantic
                  ? 'bg-teal-600 text-white border-teal-600'
                  : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
              }`}
            >
              {speteSemantic ? '✓ Căutare semantică' : 'Căutare semantică'}
            </button>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap gap-2">
            {/* Category filter */}
            <select
              className="text-sm border border-slate-300 rounded-lg px-3 py-2 bg-white focus:ring-2 focus:ring-teal-500 outline-none"
              value={speteFilterCat}
              onChange={(e) => setSpeteFilterCat(e.target.value)}
            >
              <option value="">Toate categoriile</option>
              {speteCategories.map((c) => (
                <option key={c.categorie} value={c.categorie}>
                  {c.categorie} ({c.count})
                </option>
              ))}
            </select>
            {/* Tag filter */}
            <select
              className="text-sm border border-slate-300 rounded-lg px-3 py-2 bg-white focus:ring-2 focus:ring-teal-500 outline-none"
              value={speteFilterTag}
              onChange={(e) => setSpeteFilterTag(e.target.value)}
            >
              <option value="">Toate tagurile</option>
              {speteTags.map((t) => (
                <option key={t.tag} value={t.tag}>
                  {t.tag} ({t.count})
                </option>
              ))}
            </select>
            {/* Clear filters */}
            {(speteFilterCat || speteFilterTag || speteSearch) && (
              <button
                onClick={() => { setSpeteFilterCat(''); setSpeteFilterTag(''); setSpeteSearch(''); }}
                className="text-xs text-red-500 hover:text-red-700 px-2 py-2 underline"
              >
                Șterge filtrele
              </button>
            )}
          </div>
        </header>

        {/* Results */}
        {isLoadingSpete ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={28} className="animate-spin text-teal-600" />
            <span className="ml-3 text-slate-500">Se încarcă spețele...</span>
          </div>
        ) : spete.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-400">
            <Layers size={48} className="mb-4 opacity-20" />
            <p>Nu s-au găsit spețe{speteSearch ? ` pentru "${speteSearch}"` : ''}.</p>
          </div>
        ) : (
          <>
            <div className="text-xs text-slate-500 mb-3">
              {speteTotal} spețe găsite{speteSemantic && speteSearch ? ' (ordonate după relevanță)' : ''}
            </div>
            <div className="grid gap-3 md:gap-4 grid-cols-1 lg:grid-cols-2">
              {spete.map((s: any) => (
                <div
                  key={s.numar_speta}
                  onClick={() => openSpeta(s.numar_speta)}
                  className="bg-white border border-slate-200 rounded-xl p-4 hover:border-teal-300 hover:shadow-md transition cursor-pointer group"
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <span className="text-sm font-bold text-teal-700 font-mono">Speța nr. {s.numar_speta}</span>
                    <span className="text-xs bg-teal-50 text-teal-600 px-2 py-0.5 rounded border border-teal-100 shrink-0 max-w-[200px] truncate">{s.categorie}</span>
                  </div>
                  <p className="text-sm text-slate-700 line-clamp-3 leading-relaxed mb-2">
                    {s.intrebare}
                  </p>
                  <div className="flex gap-1.5 flex-wrap">
                    {s.taguri && s.taguri.map((tag: string, i: number) => (
                      <span key={i} className="text-xs bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{tag}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {/* Pagination */}
            {spetePages > 1 && (
              <div className="flex items-center justify-center gap-2 mt-6 shrink-0">
                <button
                  onClick={() => fetchSpete(spetePage - 1, speteSearch, speteFilterCat, speteFilterTag, speteSemantic)}
                  disabled={spetePage <= 1}
                  className="px-3 py-1.5 rounded-lg border border-slate-300 text-sm disabled:opacity-40 hover:bg-slate-50"
                >
                  ← Anterior
                </button>
                <span className="text-sm text-slate-600">
                  Pagina {spetePage} / {spetePages}
                </span>
                <button
                  onClick={() => fetchSpete(spetePage + 1, speteSearch, speteFilterCat, speteFilterTag, speteSemantic)}
                  disabled={spetePage >= spetePages}
                  className="px-3 py-1.5 rounded-lg border border-slate-300 text-sm disabled:opacity-40 hover:bg-slate-50"
                >
                  Următor →
                </button>
              </div>
            )}
          </>
        )}
      </div>
    );
  };

  const renderChat = () => {
    const handleTextareaInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setChatInput(e.target.value);
      // Auto-resize: reset to 1 row then grow up to 3 rows (max ~96px)
      const ta = e.target;
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 96) + 'px';
    };

    const handleTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleChat();
      }
    };

    return (
    <div className="flex flex-col h-full bg-white">
      {/* Header */}
      <div className="border-b border-slate-100 p-4 bg-white shrink-0">
        <div className="flex justify-between items-center">
           <h2 className="font-bold text-slate-800 flex items-center gap-2">
              <MessageSquare className="text-blue-500" size={18} />
              Asistent AP
           </h2>
           <div className="flex items-center gap-2">
              {chatMessages.length > 0 && (
                <button onClick={saveConversation} className="text-xs bg-blue-50 text-blue-600 px-2.5 py-1 rounded-lg font-medium hover:bg-blue-100 transition flex items-center gap-1" title="Salvează conversația"><Save size={12} /> Salvează</button>
              )}
              <button onClick={() => loadHistory('conversations')} className="text-xs bg-slate-50 text-slate-500 px-2.5 py-1 rounded-lg font-medium hover:bg-slate-100 transition flex items-center gap-1" title="Istoric conversații"><Bookmark size={12} /> Istoric</button>
           </div>
        </div>
        {activeScopeId && (
          <div className="mt-2"><ActiveScopeIndicator /></div>
        )}
      </div>

      {/* Messages area - scrollable */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6 bg-slate-50/50 min-h-0">
        {chatMessages.length === 0 && (
           <div className="text-center text-slate-400 mt-20">
             <div className="w-16 h-16 bg-white rounded-2xl shadow-sm flex items-center justify-center mx-auto mb-6">
                <MessageSquare size={32} className="text-blue-500" />
             </div>
             <h3 className="text-slate-800 font-bold mb-2">Cu ce te pot ajuta astăzi?</h3>
             <p className="text-sm max-w-md mx-auto">Pot răspunde la întrebări despre deciziile CNSC din baza de date sau despre legislația în achiziții publice.</p>
           </div>
        )}
        {chatMessages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl p-5 shadow-sm ${
              msg.role === 'user'
                ? 'bg-slate-900 text-white rounded-br-none'
                : 'bg-white border border-slate-200 text-slate-800 rounded-bl-none prose prose-slate max-w-none'
            }`}>
              {msg.role === 'user' ? msg.text : (
                <div dangerouslySetInnerHTML={{ __html: formatMarkdown(msg.text) }} />
              )}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex justify-start">
             <div className="bg-white border border-slate-200 p-4 rounded-2xl rounded-bl-none shadow-sm flex gap-3 items-center">
               <Loader2 size={18} className="animate-spin text-blue-600" />
               <span className="text-sm text-slate-500 font-medium">{streamStatus || "Se procesează..."}</span>
             </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Sticky input area at bottom */}
      <div className="shrink-0 bg-white border-t border-slate-200 shadow-[0_-2px_8px_rgba(0,0,0,0.04)]">
        <div className="max-w-4xl mx-auto p-4 pb-3">
          {/* Scope selector + toggles */}
          <div className="flex items-center gap-3 md:gap-4 mb-3 flex-wrap">
            <div className="flex-1 min-w-0"><ScopeSelector compact /></div>
            <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none whitespace-nowrap">
              <input
                type="checkbox"
                checked={enableExpansion}
                onChange={(e) => setEnableExpansion(e.target.checked)}
                className="rounded border-slate-300 text-blue-600 focus:ring-blue-500/40 h-3.5 w-3.5"
              />
              Expansion
            </label>
            <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none whitespace-nowrap">
              <input
                type="checkbox"
                checked={enableReranking}
                onChange={(e) => setEnableReranking(e.target.checked)}
                className="rounded border-slate-300 text-blue-600 focus:ring-blue-500/40 h-3.5 w-3.5"
              />
              Reranking
            </label>
          </div>
          {/* Textarea with send button */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1 min-w-0">
              <textarea
                rows={1}
                className={`w-full border rounded-xl pl-5 pr-4 py-3.5 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm resize-none overflow-y-auto leading-relaxed ${chatInput.length > 100000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                style={{ maxHeight: '96px', minHeight: '48px' }}
                placeholder={activeScopeId ? "Caută în scope-ul selectat..." : "Scrie mesajul tău... (Shift+Enter = linie nouă)"}
                value={chatInput}
                onChange={handleTextareaInput}
                onKeyDown={handleTextareaKeyDown}
              />
            </div>
            <button
              onClick={handleChat}
              disabled={isLoading || !chatInput.trim()}
              className="shrink-0 self-center bg-blue-600 text-white p-2.5 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition flex items-center justify-center"
            >
              <Send size={16} />
            </button>
          </div>
          {chatInput.length > 1000 && <CharCounter value={chatInput} maxLength={100000} />}
        </div>
        <p className="text-center text-xs text-slate-400 pb-2">AI-ul poate face greșeli. Verifică informațiile importante.</p>
      </div>
    </div>
    );
  };

  return (
    <div className="flex h-screen bg-slate-50 font-sans text-slate-900">
      {renderSidebar()}

      {/* Auth Modal */}
      {showAuthModal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-slate-900 rounded-2xl shadow-2xl w-full max-w-md mx-4 border border-slate-700">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-white">
                  {authMode === 'login' ? 'Autentificare' : authMode === 'register' ? 'Creează cont' : authMode === 'forgotPassword' ? 'Resetare parolă' : 'Parolă nouă'}
                </h2>
                <button onClick={() => { setShowAuthModal(false); setAuthError(''); setForgotPasswordMessage(''); }} className="text-slate-400 hover:text-white">
                  <X size={20} />
                </button>
              </div>

              {authError && (
                <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                  {authError}
                </div>
              )}

              {forgotPasswordMessage && (
                <div className="mb-4 p-3 rounded-lg bg-green-500/10 border border-green-500/30 text-green-400 text-sm">
                  {forgotPasswordMessage}
                </div>
              )}

              {/* LOGIN MODE */}
              {authMode === 'login' && (
                <div className="space-y-4">
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Email</label>
                    <input
                      type="email"
                      value={authEmail}
                      onChange={e => setAuthEmail(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="email@exemplu.ro"
                      onKeyDown={e => e.key === 'Enter' && handleLogin()}
                    />
                  </div>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Parolă</label>
                    <input
                      type="password"
                      value={authPassword}
                      onChange={e => setAuthPassword(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="Minim 8 caractere"
                      onKeyDown={e => e.key === 'Enter' && handleLogin()}
                    />
                  </div>
                  <div className="text-right">
                    <button onClick={() => { setAuthMode('forgotPassword'); setAuthError(''); setForgotPasswordMessage(''); }} className="text-sm text-blue-400 hover:underline">
                      Am uitat parola
                    </button>
                  </div>
                  <button
                    onClick={handleLogin}
                    disabled={authLoading}
                    className="w-full py-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    {authLoading && <Loader2 size={16} className="animate-spin" />}
                    Autentifică-te
                  </button>
                  <div className="text-center">
                    <p className="text-sm text-slate-400">
                      Nu ai cont?{' '}
                      <button onClick={() => { setAuthMode('register'); setAuthError(''); setForgotPasswordMessage(''); }} className="text-blue-400 hover:underline">
                        Creează cont
                      </button>
                    </p>
                  </div>
                </div>
              )}

              {/* REGISTER MODE */}
              {authMode === 'register' && (
                <div className="space-y-4">
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Nume</label>
                    <input
                      type="text"
                      value={authNume}
                      onChange={e => setAuthNume(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="Numele dvs."
                    />
                  </div>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Email</label>
                    <input
                      type="email"
                      value={authEmail}
                      onChange={e => setAuthEmail(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="email@exemplu.ro"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Parolă</label>
                    <input
                      type="password"
                      value={authPassword}
                      onChange={e => setAuthPassword(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="Minim 8 caractere"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Confirmă parola</label>
                    <input
                      type="password"
                      value={authConfirmPassword}
                      onChange={e => setAuthConfirmPassword(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="Repetă parola"
                      onKeyDown={e => e.key === 'Enter' && handleRegister()}
                    />
                  </div>
                  <button
                    onClick={handleRegister}
                    disabled={authLoading}
                    className="w-full py-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    {authLoading && <Loader2 size={16} className="animate-spin" />}
                    Creează cont
                  </button>
                  <div className="text-center">
                    <p className="text-sm text-slate-400">
                      Ai deja cont?{' '}
                      <button onClick={() => { setAuthMode('login'); setAuthError(''); }} className="text-blue-400 hover:underline">
                        Autentifică-te
                      </button>
                    </p>
                  </div>
                </div>
              )}

              {/* FORGOT PASSWORD MODE */}
              {authMode === 'forgotPassword' && (
                <div className="space-y-4">
                  <p className="text-sm text-slate-400">Introdu adresa de email asociată contului tău și vei primi un cod de resetare.</p>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Email</label>
                    <input
                      type="email"
                      value={authEmail}
                      onChange={e => setAuthEmail(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="email@exemplu.ro"
                      onKeyDown={e => e.key === 'Enter' && handleForgotPassword()}
                    />
                  </div>
                  <button
                    onClick={handleForgotPassword}
                    disabled={authLoading}
                    className="w-full py-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    {authLoading && <Loader2 size={16} className="animate-spin" />}
                    Trimite codul de resetare
                  </button>
                  <button
                    onClick={() => { setAuthMode('resetPassword'); setAuthError(''); setForgotPasswordMessage(''); }}
                    className="w-full py-2 text-sm text-blue-400 hover:underline"
                  >
                    Am primit codul — introdu parola nouă
                  </button>
                  <div className="text-center">
                    <button onClick={() => { setAuthMode('login'); setAuthError(''); setForgotPasswordMessage(''); }} className="text-sm text-slate-400 hover:text-white">
                      ← Înapoi la autentificare
                    </button>
                  </div>
                </div>
              )}

              {/* RESET PASSWORD MODE */}
              {authMode === 'resetPassword' && (
                <div className="space-y-4">
                  <p className="text-sm text-slate-400">Introdu codul primit pe email și noua parolă.</p>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Cod de resetare</label>
                    <input
                      type="text"
                      value={resetToken}
                      onChange={e => setResetToken(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm font-mono focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="Codul din email"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Parolă nouă</label>
                    <input
                      type="password"
                      value={resetNewPassword}
                      onChange={e => setResetNewPassword(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="Minim 8 caractere"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-slate-400 mb-1 block">Confirmă parola nouă</label>
                    <input
                      type="password"
                      value={resetConfirmPassword}
                      onChange={e => setResetConfirmPassword(e.target.value)}
                      className="w-full p-3 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                      placeholder="Repetă parola nouă"
                      onKeyDown={e => e.key === 'Enter' && handleResetPassword()}
                    />
                  </div>
                  <button
                    onClick={handleResetPassword}
                    disabled={authLoading}
                    className="w-full py-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    {authLoading && <Loader2 size={16} className="animate-spin" />}
                    Resetează parola
                  </button>
                  <div className="text-center">
                    <button onClick={() => { setAuthMode('login'); setAuthError(''); setForgotPasswordMessage(''); }} className="text-sm text-slate-400 hover:text-white">
                      ← Înapoi la autentificare
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 overflow-hidden relative shadow-2xl z-10 md:rounded-l-2xl md:border-l border-slate-200/50 bg-white md:ml-[-1px] pt-[52px] md:pt-0 flex flex-col">
        <div className="flex-1 overflow-hidden flex flex-col">
        {mode === 'dashboard' && renderDashboard()}
        {mode === 'datalake' && renderDataLake()}
        {mode === 'spete' && renderSpeteANAP()}
        {mode === 'drafter' && renderDrafter()}
        {mode === 'chat' && renderChat()}
        {mode === 'redflags' && (
          <div className="h-full flex flex-col md:flex-row bg-white">
            {/* Left panel — input */}
            <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-lg font-bold text-slate-800 flex gap-2 items-center">
                  <AlertTriangle className="text-red-500" size={20}/> Red Flags Detector
                </h2>
                <button onClick={() => loadHistory('redflags')} className="text-xs bg-slate-50 text-slate-500 px-2.5 py-1 rounded-lg font-medium hover:bg-slate-100 transition flex items-center gap-1" title="Istoric"><Bookmark size={12} /> Istoric</button>
              </div>
              <p className="text-xs text-slate-500 mb-4">Identifică clauze restrictive în documentația de achiziții publice.</p>

              {renderActiveDosarBanner((docs) => {
                setUploadedDocsRedFlags(prev => [...prev, ...docs]);
                setRedFlagsTab('upload');
              })}

              {/* Tabs */}
              <div className="flex gap-1 mb-4 border-b border-slate-200 mt-2">
                <button
                  onClick={() => setRedFlagsTab('manual')}
                  className={`px-3 py-2 text-sm font-medium transition border-b-2 ${
                    redFlagsTab === 'manual'
                      ? 'border-red-600 text-red-600'
                      : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}
                >
                  Manual Input
                </button>
                <button
                  onClick={() => setRedFlagsTab('upload')}
                  className={`px-3 py-2 text-sm font-medium transition border-b-2 ${
                    redFlagsTab === 'upload'
                      ? 'border-red-600 text-red-600'
                      : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}
                >
                  Upload Document
                </button>
              </div>

              {/* Manual Input Tab */}
              {redFlagsTab === 'manual' && (
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                      Documentație Achiziție
                    </label>
                    <textarea
                      className={`w-full p-3 border rounded-lg h-48 text-sm focus:ring-2 focus:ring-red-500 outline-none transition shadow-sm font-mono ${redFlagsText.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                      placeholder="Introduceți sau lipiți conținutul documentației..."
                      value={redFlagsText}
                      onChange={(e) => setRedFlagsText(e.target.value)}
                    />
                    <CharCounter value={redFlagsText} maxLength={200000} />
                  </div>
                  <button
                    onClick={handleRedFlags}
                    disabled={isLoading || !redFlagsText.trim()}
                    className="w-full bg-red-600 text-white py-3 rounded-xl font-medium hover:bg-red-700 transition disabled:opacity-50 flex items-center gap-2 justify-center shadow-lg hover:shadow-xl"
                  >
                    {isLoading ? <Loader2 className="animate-spin" size={18} /> : <AlertTriangle size={18} />}
                    {isLoading ? 'Analizare în curs...' : 'Analizează Red Flags'}
                  </button>
                  {isLoading && redFlagsProgress && (
                    <p className="text-sm text-slate-500 text-center animate-pulse">
                      {redFlagsProgress}
                    </p>
                  )}
                </div>
              )}

              {/* Upload Document Tab */}
              {redFlagsTab === 'upload' && (
                <div className="space-y-4">
                  <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
                    <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
                      Încarcă Document (.txt, .md, .pdf)
                    </label>
                    <input
                      type="file"
                      accept=".txt,.md,.pdf,.doc,.docx"
                      onChange={(e) => handleDocumentUpload(e, undefined, (doc) => setUploadedDocsRedFlags(prev => [...prev, doc]))}
                      className="block w-full text-sm text-slate-600
                        file:mr-4 file:py-1.5 file:px-3
                        file:rounded-lg file:border-0
                        file:text-xs file:font-semibold
                        file:bg-red-50 file:text-red-700
                        hover:file:bg-red-100"
                    />
                    {uploadedDocsRedFlags.length > 0 && (
                      <div className="mt-2 space-y-1">
                        {uploadedDocsRedFlags.map((doc, idx) => (
                          <div key={idx} className="flex items-center justify-between text-xs text-green-600 bg-green-50 rounded px-2 py-1">
                            <span>✓ {doc.name} ({doc.text.length.toLocaleString()} car.)</span>
                            <button onClick={() => setUploadedDocsRedFlags(prev => prev.filter((_, i) => i !== idx))} className="text-red-400 hover:text-red-600 ml-2" title="Șterge">✕</button>
                          </div>
                        ))}
                        {uploadedDocsRedFlags.length > 1 && (
                          <button onClick={() => setUploadedDocsRedFlags([])} className="text-xs text-red-500 hover:text-red-700 underline">Șterge toate</button>
                        )}
                      </div>
                    )}
                  </div>
                  <button
                    onClick={handleRedFlags}
                    disabled={isLoading || uploadedDocsRedFlags.length === 0}
                    className="w-full bg-red-600 text-white py-3 rounded-xl font-medium hover:bg-red-700 transition disabled:opacity-50 flex items-center gap-2 justify-center shadow-lg hover:shadow-xl"
                  >
                    {isLoading ? <Loader2 className="animate-spin" size={18} /> : <AlertTriangle size={18} />}
                    {isLoading ? 'Analizare în curs...' : 'Analizează Red Flags'}
                  </button>
                  {isLoading && redFlagsProgress && (
                    <p className="text-sm text-slate-500 text-center animate-pulse">
                      {redFlagsProgress}
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* Right panel — results */}
            <div className="w-full md:w-2/3 p-4 md:p-8 overflow-y-auto bg-white">
              {redFlagsResults.length > 0 ? (
                <div className="space-y-4">
                  <div className="bg-white p-4 rounded-lg border border-slate-200 sticky top-0 z-10 space-y-2">
                    <div className="flex items-center justify-between">
                      <h3 className="font-bold text-slate-800">Rezultate Analiză</h3>
                      <div className="flex gap-4 text-sm">
                        <span className="text-red-600 font-bold">
                          {redFlagsResults.filter(rf => rf.severity === 'CRITICĂ').length} Critice
                        </span>
                        <span className="text-orange-600 font-bold">
                          {redFlagsResults.filter(rf => rf.severity === 'MEDIE').length} Medii
                        </span>
                        <span className="text-yellow-600 font-bold">
                          {redFlagsResults.filter(rf => rf.severity === 'SCĂZUTĂ').length} Scăzute
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center justify-between border-t border-slate-100 pt-2">
                      <div className="flex gap-2 items-center text-xs">
                        <button
                          onClick={() => setSelectedRedFlags(redFlagsResults.map((_, i) => i))}
                          className="text-blue-600 hover:text-blue-800 underline"
                        >Selectează toate</button>
                        <span className="text-slate-300">|</span>
                        <button
                          onClick={() => setSelectedRedFlags([])}
                          className="text-blue-600 hover:text-blue-800 underline"
                        >Deselectează</button>
                        {selectedRedFlags.length > 0 && (
                          <span className="text-slate-500 ml-2">{selectedRedFlags.length} selectate</span>
                        )}
                      </div>
                      <div className="flex gap-1.5">
                        {(['docx', 'pdf', 'md'] as const).map(fmt => (
                          <button
                            key={fmt}
                            onClick={() => handleRedFlagsExport(fmt)}
                            disabled={selectedRedFlags.length === 0}
                            className="text-xs bg-purple-600 text-white px-2.5 py-1.5 rounded-lg font-medium hover:bg-purple-700 transition disabled:opacity-40 flex items-center gap-1"
                          >
                            <Download size={11} />
                            {fmt.toUpperCase()}
                          </button>
                        ))}
                        <button onClick={saveRedFlags} className="text-xs bg-green-600 text-white px-2.5 py-1.5 rounded-lg font-medium hover:bg-green-700 transition flex items-center gap-1"><Save size={11} /> Salvează</button>
                        <button onClick={() => loadHistory('redflags')} className="text-xs bg-slate-100 text-slate-600 px-2.5 py-1.5 rounded-lg font-medium hover:bg-slate-200 transition flex items-center gap-1"><Bookmark size={11} /> Istoric</button>
                      </div>
                    </div>
                  </div>

                  {redFlagsResults.map((flag, idx) => (
                    <div
                      key={idx}
                      className={`bg-white p-6 rounded-xl border-l-4 shadow-sm ${
                        flag.severity === 'CRITICĂ'
                          ? 'border-red-500'
                          : flag.severity === 'MEDIE'
                          ? 'border-orange-500'
                          : 'border-yellow-500'
                      }`}
                    >
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            checked={selectedRedFlags.includes(idx)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedRedFlags(prev => [...prev, idx]);
                              } else {
                                setSelectedRedFlags(prev => prev.filter(i => i !== idx));
                              }
                            }}
                            className="w-4 h-4 text-purple-600 border-slate-300 rounded focus:ring-purple-500 cursor-pointer"
                          />
                          <AlertTriangle
                            className={
                              flag.severity === 'CRITICĂ'
                                ? 'text-red-500'
                                : flag.severity === 'MEDIE'
                                ? 'text-orange-500'
                                : 'text-yellow-500'
                            }
                            size={20}
                          />
                          <span
                            className={`text-xs px-3 py-1 rounded-full font-bold ${
                              flag.severity === 'CRITICĂ'
                                ? 'bg-red-100 text-red-700'
                                : flag.severity === 'MEDIE'
                                ? 'bg-orange-100 text-orange-700'
                                : 'bg-yellow-100 text-yellow-700'
                            }`}
                          >
                            {flag.severity}
                          </span>
                        </div>
                      </div>

                      <div className="space-y-3 text-sm">
                        <div>
                          <p className="font-semibold text-slate-700 mb-1">Clauza Problematica:</p>
                          <p className="bg-slate-50 p-3 rounded border border-slate-200 italic text-slate-600">
                            "{flag.clause}"
                          </p>
                        </div>

                        <div>
                          <p className="font-semibold text-slate-700 mb-1">Problema:</p>
                          <p className="text-slate-600">{flag.issue}</p>
                        </div>

                        {flag.legal_references && flag.legal_references.length > 0 && (
                          <div>
                            <p className="font-semibold text-slate-700 mb-1">Temei Legal:</p>
                            <div className="space-y-2">
                              {flag.legal_references.map((ref: any, refIdx: number) => (
                                <details key={refIdx} className="group">
                                  <summary className="cursor-pointer flex items-center gap-2 text-slate-700 hover:text-blue-700">
                                    <span className="font-mono text-xs bg-emerald-50 text-emerald-700 px-2 py-1 rounded border border-emerald-200">
                                      {ref.citare} din {ref.act_normativ}
                                    </span>
                                    <ChevronDown size={14} className="group-open:rotate-180 transition-transform text-slate-400" />
                                  </summary>
                                  {ref.text_extras && (
                                    <p className="mt-1 ml-2 p-2 bg-emerald-50/50 rounded text-xs text-slate-600 border-l-2 border-emerald-300">
                                      {ref.text_extras}
                                    </p>
                                  )}
                                </details>
                              ))}
                            </div>
                          </div>
                        )}

                        {flag.recommendation && (
                          <div>
                            <p className="font-semibold text-slate-700 mb-1">Recomandare:</p>
                            <p className="text-slate-600">{flag.recommendation}</p>
                          </div>
                        )}

                        {flag.decision_refs && flag.decision_refs.length > 0 && (
                          <div>
                            <p className="font-semibold text-slate-700 mb-1">Jurisprudenta CNSC:</p>
                            <div className="flex gap-2 flex-wrap">
                              {flag.decision_refs.map((ref: string) => (
                                <button
                                  key={ref}
                                  onClick={() => openDecision(ref)}
                                  className="text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded border border-blue-200 font-mono hover:bg-blue-100 hover:border-blue-400 cursor-pointer transition"
                                  title="Click pentru a vizualiza decizia"
                                >
                                  {ref}
                                </button>
                              ))}
                            </div>
                          </div>
                        )}

                        {(!flag.legal_references || flag.legal_references.length === 0) &&
                         (!flag.decision_refs || flag.decision_refs.length === 0) && (
                          <p className="text-xs text-slate-400 italic">
                            Nu s-au gasit referinte legislative sau jurisprudenta CNSC relevanta in baza de date
                          </p>
                        )}

                        {/* Clarification Proposal — editable inline */}
                        {(flag.clarification_proposal || editedClarifications[idx]) && (
                          <div className="mt-3 bg-violet-50 border border-violet-200 rounded-lg p-4">
                            <div className="flex items-center justify-between mb-2">
                              <p className="font-semibold text-violet-800 text-xs uppercase tracking-wide">Propunere Clarificare</p>
                              {editingClarificationIdx === idx ? (
                                <button
                                  onClick={() => setEditingClarificationIdx(null)}
                                  className="text-xs bg-violet-600 text-white px-2.5 py-1 rounded hover:bg-violet-700 transition flex items-center gap-1"
                                >
                                  <Save size={11} /> Salvează
                                </button>
                              ) : (
                                <button
                                  onClick={() => {
                                    if (!editedClarifications[idx]) {
                                      setEditedClarifications(prev => ({...prev, [idx]: flag.clarification_proposal || ''}));
                                    }
                                    setEditingClarificationIdx(idx);
                                  }}
                                  className="text-xs bg-violet-100 text-violet-700 px-2.5 py-1 rounded hover:bg-violet-200 transition flex items-center gap-1"
                                >
                                  <Pencil size={11} /> Editează
                                </button>
                              )}
                            </div>
                            {editingClarificationIdx === idx ? (
                              <textarea
                                value={editedClarifications[idx] ?? flag.clarification_proposal ?? ''}
                                onChange={(e) => setEditedClarifications(prev => ({...prev, [idx]: e.target.value}))}
                                className="w-full min-h-[120px] p-3 border border-violet-300 rounded-lg text-sm text-slate-700 font-serif leading-relaxed resize-y focus:ring-2 focus:ring-violet-400 focus:border-violet-400"
                              />
                            ) : (
                              <p className="text-sm text-slate-700 font-serif leading-relaxed whitespace-pre-wrap">
                                {editedClarifications[idx] ?? flag.clarification_proposal}
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}

                  {/* Export info */}
                  {selectedRedFlags.length > 0 && (
                    <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 mt-4 text-center">
                      <p className="text-sm text-purple-700">
                        <strong>{selectedRedFlags.length}</strong> red flag{selectedRedFlags.length > 1 ? '-uri' : ''} selectat{selectedRedFlags.length > 1 ? 'e' : ''} pentru export.
                        Editați propunerile de clarificare din fiecare card, apoi exportați folosind butoanele DOCX / PDF / MD de mai sus.
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-300">
                  <AlertTriangle size={48} className="mb-4 opacity-20" />
                  <p className="text-lg font-medium">Rezultatele analizei vor apărea aici</p>
                  <p className="text-sm mt-2">
                    Introduceți text sau încărcați un document pentru a începe analiza
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
        {mode === 'clarification' && handleClarification && (
          <div className="h-full flex flex-col md:flex-row bg-white">
            {/* Left panel — input */}
            <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-lg font-bold text-slate-800 flex gap-2 items-center">
                  <Search className="text-purple-600" size={20}/> Asistent Clarificări
                </h2>
                <button onClick={() => loadHistory('clarificare')} className="text-xs bg-slate-50 text-slate-500 px-2.5 py-1 rounded-lg font-medium hover:bg-slate-100 transition flex items-center gap-1" title="Istoric"><Bookmark size={12} /> Istoric</button>
              </div>

              {renderActiveDosarBanner((docs) => {
                setUploadedDocsClarification(prev => [...prev, ...docs]);
                const combined = docs.map((d, i) => `=== DOCUMENT ${i+1}: ${d.name} ===\n${d.text}`).join('\n\n---\n\n');
                setClarificationClause(prev => prev ? prev + '\n\n---\n\n' + combined : combined);
              })}

              <div className="space-y-4 mt-2">
                <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
                  <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
                    Încarcă document (.txt, .md, .pdf)
                  </label>
                  <input
                    type="file"
                    accept=".txt,.md,.pdf,.doc,.docx"
                    onChange={(e) => handleDocumentUpload(e, (text) => setClarificationClause(prev => prev ? prev + '\n\n---\n\n' + text : text), (doc) => setUploadedDocsClarification(prev => [...prev, doc]))}
                    className="block w-full text-sm text-slate-600
                      file:mr-4 file:py-1.5 file:px-3
                      file:rounded-lg file:border-0
                      file:text-xs file:font-semibold
                      file:bg-purple-50 file:text-purple-700
                      hover:file:bg-purple-100"
                  />
                  {uploadedDocsClarification.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {uploadedDocsClarification.map((doc, idx) => (
                        <div key={idx} className="flex items-center justify-between text-xs text-green-600 bg-green-50 rounded px-2 py-1">
                          <span>✓ {doc.name} ({doc.text.length.toLocaleString()} car.)</span>
                          <button onClick={() => setUploadedDocsClarification(prev => prev.filter((_, i) => i !== idx))} className="text-red-400 hover:text-red-600 ml-2" title="Șterge">✕</button>
                        </div>
                      ))}
                      {uploadedDocsClarification.length > 1 && (
                        <button onClick={() => { setUploadedDocsClarification([]); setClarificationClause(''); }} className="text-xs text-red-500 hover:text-red-700 underline">Șterge toate</button>
                      )}
                    </div>
                  )}
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Clauza Problematică</label>
                  <textarea
                    className={`w-full p-3 border rounded-lg text-sm h-32 focus:ring-2 focus:ring-purple-500 outline-none transition shadow-sm ${clarificationClause.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                    placeholder="Paste text din documentație sau încarcă un document..."
                    value={clarificationClause}
                    onChange={(e) => setClarificationClause(e.target.value)}
                  />
                  <CharCounter value={clarificationClause} maxLength={200000} />
                </div>
                <button
                  onClick={handleClarification}
                  disabled={isLoading || !clarificationClause}
                  className="w-full bg-purple-600 text-white py-3 rounded-xl font-medium hover:bg-purple-700 transition flex justify-center items-center gap-2 shadow-lg hover:shadow-xl"
                >
                  {isLoading ? <Loader2 className="animate-spin" size={18} /> : "Generează Cerere Clarificare"}
                </button>
              </div>
            </div>
            {/* Right panel — output */}
            <div className="w-full md:w-2/3 p-4 md:p-10 overflow-y-auto bg-white">
              {generatedContent ? (
                <div>
                  <div className="flex justify-end gap-2 mb-4">
                    <button onClick={() => saveDocument('clarificare', clarificationClause.slice(0, 200) || 'Clarificare', generatedContent, generatedDecisionRefs, { clauza_originala: clarificationClause })} className="text-xs text-green-600 font-medium hover:underline flex items-center gap-1"><Save size={12} /> Salvează</button>
                    <button onClick={() => loadHistory('clarificare')} className="text-xs text-slate-500 font-medium hover:underline flex items-center gap-1"><Bookmark size={12} /> Istoric</button>
                  </div>
                  <div className="prose prose-slate max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(generatedContent) }} />
                  {generatedDecisionRefs.length > 0 && (
                    <div className="mt-6 p-4 bg-purple-50 border border-purple-200 rounded-xl">
                      <p className="font-semibold text-slate-700 mb-2 text-sm">Jurisprudență CNSC utilizată:</p>
                      <div className="flex flex-wrap gap-2">
                        {generatedDecisionRefs.map((ref: string) => (
                          <span key={ref} className="text-xs bg-white text-purple-700 px-3 py-1.5 rounded-lg border border-purple-200 font-mono cursor-pointer hover:bg-purple-100 transition" onClick={() => openDecision(ref)}>{ref}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-300">
                  <Search size={48} className="mb-4 opacity-20"/>
                  <p className="text-lg font-medium">Rezultatul va apărea aici</p>
                  <p className="text-sm mt-2">Încarcă un document sau introdu textul clauzei problematice</p>
                </div>
              )}
            </div>
          </div>
        )}
        {mode === 'rag' && handleRAGMemo && (
           <div className="h-full flex flex-col p-6">
              <header className="mb-4 flex items-center justify-between">
                 <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2"><BookOpen className="text-teal-600"/> Jurisprudență RAG</h2>
                 <button onClick={() => loadHistory('rag_memo')} className="text-xs bg-slate-50 text-slate-500 px-2.5 py-1 rounded-lg font-medium hover:bg-slate-100 transition flex items-center gap-1" title="Istoric"><Bookmark size={12} /> Istoric</button>
              </header>
              <ActiveScopeIndicator />
              <div className="flex flex-col md:flex-row gap-4 md:gap-6 flex-1 overflow-hidden">
                 <div className="w-full md:w-80 shrink-0 flex flex-col gap-4">
                    <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                       <ScopeSelector compact />
                       <div className="bg-slate-50 p-3 rounded-lg border border-dashed border-slate-300 mb-3">
                         <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
                           Încarcă document (.txt, .md, .pdf)
                         </label>
                         <input
                           type="file"
                           accept=".txt,.md,.pdf,.doc,.docx"
                           onChange={(e) => handleDocumentUpload(e, (text) => setMemoTopic(prev => prev ? prev + '\n\n---\n\n' + text : text), (doc) => setUploadedDocsRag(prev => [...prev, doc]))}
                           className="block w-full text-sm text-slate-600
                             file:mr-4 file:py-1.5 file:px-3
                             file:rounded-lg file:border-0
                             file:text-xs file:font-semibold
                             file:bg-teal-50 file:text-teal-700
                             hover:file:bg-teal-100"
                         />
                         {uploadedDocsRag.length > 0 && (
                           <div className="mt-2 space-y-1">
                             {uploadedDocsRag.map((doc, idx) => (
                               <div key={idx} className="flex items-center justify-between text-xs text-green-600 bg-green-50 rounded px-2 py-1">
                                 <span>✓ {doc.name} ({doc.text.length.toLocaleString()} car.)</span>
                                 <button onClick={() => setUploadedDocsRag(prev => prev.filter((_, i) => i !== idx))} className="text-red-400 hover:text-red-600 ml-2" title="Șterge">✕</button>
                               </div>
                             ))}
                             {uploadedDocsRag.length > 1 && (
                               <button onClick={() => { setUploadedDocsRag([]); setMemoTopic(''); }} className="text-xs text-red-500 hover:text-red-700 underline">Șterge toate</button>
                             )}
                           </div>
                         )}
                       </div>
                       <label className="text-sm font-bold text-slate-700 block mb-2">Subiect Memo</label>
                       <textarea
                          className={`w-full border rounded-lg p-3 text-sm h-24 focus:ring-2 focus:ring-teal-500 outline-none ${memoTopic.length > 100000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                          placeholder="Ex: Respingere ofertă sau încarcă document..."
                          value={memoTopic}
                          onChange={(e) => setMemoTopic(e.target.value)}
                       />
                       <CharCounter value={memoTopic} maxLength={100000} />
                       <button
                          onClick={handleRAGMemo}
                          disabled={isLoading || !memoTopic.trim()}
                          className="w-full bg-teal-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-teal-700 transition disabled:opacity-50"
                       >
                          {isLoading ? (streamStatus || "Analiză...") : "Generează Memo"}
                       </button>
                       <p className="text-xs text-slate-400 mt-3 text-center">Căutare semantică în {dbStats?.total_decisions || 0} decizii din baza de date.</p>
                    </div>
                 </div>
                 <div className="flex-1 bg-white border border-slate-200 rounded-xl shadow-sm p-4 md:p-8 overflow-y-auto text-slate-800 leading-relaxed">
                    {generatedContent ? (
                       <div>
                         <div className="flex justify-end gap-2 mb-4 flex-wrap">
                           <button
                             onClick={async () => {
                               if (ragMemoSaved) return;
                               await saveDocument('rag_memo', memoTopic.slice(0, 200) || 'Memo RAG', generatedContent, generatedDecisionRefs, { topic: memoTopic });
                               setRagMemoSaved(true);
                             }}
                             disabled={ragMemoSaved}
                             className={`text-xs font-medium flex items-center gap-1 ${ragMemoSaved ? 'text-green-400 cursor-default' : 'text-green-600 hover:underline'}`}
                           >
                             <Save size={12} /> {ragMemoSaved ? 'Salvat ✓' : 'Salvează'}
                           </button>
                           {(['docx', 'pdf', 'md'] as const).map(fmt => (
                             <button key={fmt} onClick={() => handleRAGMemoExport(fmt)} className="text-xs text-blue-600 font-medium hover:underline flex items-center gap-1">
                               <Download size={12} /> {fmt.toUpperCase()}
                             </button>
                           ))}
                           <button onClick={() => loadHistory('rag_memo')} className="text-xs text-slate-500 font-medium hover:underline flex items-center gap-1"><Bookmark size={12} /> Istoric</button>
                         </div>
                         <div className="prose prose-slate max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(generatedContent) }} />
                       </div>
                    ) : (
                       <div className="flex flex-col items-center justify-center h-full text-slate-300">
                          <BookOpen size={48} className="mb-4 opacity-20"/>
                          <p>Rezultatul RAG va apărea aici.</p>
                       </div>
                    )}
                 </div>
              </div>
           </div>
        )}
        {mode === 'strategy' && renderStrategy()}
        {mode === 'compliance' && renderCompliance()}
        {mode === 'multi_document' && renderMultiDocument()}
        {mode === 'analytics' && renderAnalytics()}
        {mode === 'training' && renderTraining()}
        {mode === 'dosare' && renderDosare()}
        {mode === 'alerts' && renderAlerts()}
        {mode === 'settings' && renderSettings()}
        {mode === 'profile' && renderProfile()}
        {mode === 'pricing' && renderPricing()}
        </div>

        {/* Save Toast */}
        {saveStatus && (
          <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-sm font-medium flex items-center gap-2 ${
            saveStatus.type === 'success' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'
          }`}>
            {saveStatus.type === 'success' ? <CheckCircle size={16} /> : <XCircle size={16} />}
            {saveStatus.text}
          </div>
        )}

        {/* Global History Panel */}
        {historyPanel && (
          <HistoryPanel
            items={historyItems}
            loading={historyLoading}
            type={historyPanel}
            onLoad={(item) => {
              if (historyPanel === 'conversations') loadConversation(item.id);
              else if (historyPanel === 'redflags') loadRedFlags(item.id);
              else if (historyPanel === 'training') loadTraining(item.id);
              else if (historyPanel === 'contestatie') loadDocument(item.id, 'drafter');
              else if (historyPanel === 'clarificare') loadDocument(item.id, 'clarification');
              else if (historyPanel === 'rag_memo') loadDocument(item.id, 'rag');
            }}
            onDelete={(id) => deleteHistoryItem(historyPanel, id)}
            onClose={() => setHistoryPanel(null)}
          />
        )}

        {/* Sticky Install Banner - Mobile PWA */}
        {showInstallBanner && (
        <div className="shrink-0 bg-gradient-to-r from-blue-600 to-purple-600 text-white px-4 py-2.5 flex items-center gap-3 md:hidden shadow-lg">
          <Smartphone size={18} className="shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-bold">Instalează ExpertAP</p>
            <p className="text-[10px] opacity-80 truncate">Acces rapid de pe ecranul principal</p>
          </div>
          <button
            onClick={() => {
              const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
              const msg = isIOS
                ? '1. Apasă butonul Share (⎙) din Safari\n2. Alege "Add to Home Screen"\n3. Confirmă cu "Add"'
                : '1. Apasă meniul ⋮ din Chrome\n2. Alege "Add to Home screen" / "Install app"\n3. Confirmă instalarea';
              alert(`Instalare ExpertAP pe telefon:\n\n${msg}`);
            }}
            className="shrink-0 bg-white text-blue-700 text-xs font-bold px-3 py-1.5 rounded-lg hover:bg-blue-50 transition"
          >
            Instalează
          </button>
          <button onClick={() => { setShowInstallBanner(false); try { localStorage.setItem('expertap-install-dismissed', '1'); } catch {} }} className="shrink-0 text-white/70 hover:text-white">
            <X size={14} />
          </button>
        </div>
        )}
      </main>

      {/* Scope Manager Modal */}
      {showScopeManager && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => { setShowScopeManager(false); setEditingScope(null); }}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6 max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-slate-800 flex items-center gap-2">
                <Bookmark size={20} className="text-blue-600" />
                Gestionare Scope-uri
              </h3>
              <button onClick={() => { setShowScopeManager(false); setEditingScope(null); }} className="text-slate-400 hover:text-slate-600">
                <X size={20} />
              </button>
            </div>

            {scopes.length === 0 ? (
              <div className="text-center py-12 text-slate-400">
                <Bookmark size={32} className="mx-auto mb-3 opacity-30" />
                <p className="text-sm">Nu ai niciun scope salvat.</p>
                <p className="text-xs mt-1">Mergi pe pagina Decizii CNSC, aplică filtre, apoi apasă "Salvează Scope".</p>
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto space-y-3">
                {scopes.map(s => (
                  <div key={s.id} className={`border rounded-xl p-4 transition ${activeScopeId === s.id ? 'border-blue-400 bg-blue-50/50' : 'border-slate-200 bg-white'}`}>
                    {editingScope?.id === s.id ? (
                      /* Editing mode */
                      <div className="space-y-2">
                        <input
                          type="text"
                          className="w-full border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500/40 outline-none"
                          value={editingScope.name}
                          onChange={(e) => setEditingScope({ ...editingScope, name: e.target.value })}
                          autoFocus
                        />
                        <input
                          type="text"
                          className="w-full border border-slate-300 rounded-lg px-3 py-1.5 text-xs focus:ring-2 focus:ring-blue-500/40 outline-none"
                          placeholder="Descriere (opțional)"
                          value={editingScope.description || ''}
                          onChange={(e) => setEditingScope({ ...editingScope, description: e.target.value || null })}
                        />
                        <div className="flex gap-2 pt-1">
                          <button
                            onClick={() => updateScope(editingScope.id, editingScope.name, editingScope.description)}
                            disabled={!editingScope.name.trim()}
                            className="text-xs bg-blue-600 text-white px-3 py-1.5 rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1"
                          >
                            <Save size={12} /> Salvează
                          </button>
                          <button
                            onClick={() => setEditingScope(null)}
                            className="text-xs border border-slate-300 text-slate-600 px-3 py-1.5 rounded-lg hover:bg-slate-50"
                          >
                            Anulează
                          </button>
                        </div>
                      </div>
                    ) : (
                      /* Display mode */
                      <div>
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="font-semibold text-sm text-slate-800 truncate">{s.name}</div>
                            {s.description && <div className="text-xs text-slate-500 mt-0.5 truncate">{s.description}</div>}
                          </div>
                          <span className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full whitespace-nowrap shrink-0">
                            {s.decision_count} decizii
                          </span>
                        </div>
                        {/* Filter pills */}
                        <div className="flex flex-wrap gap-1 mt-2">
                          {s.filters.ruling && s.filters.ruling.split(',').map((r: string) => <span key={r} className="text-[10px] bg-green-50 text-green-700 border border-green-200 rounded-full px-2 py-0.5">{r === '__NULL__' ? 'Fără soluție' : r}</span>)}
                          {s.filters.tip_contestatie && <span className="text-[10px] bg-purple-50 text-purple-700 border border-purple-200 rounded-full px-2 py-0.5">{s.filters.tip_contestatie}</span>}
                          {s.filters.years?.map((y: number) => <span key={y} className="text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-2 py-0.5">{y}</span>)}
                          {s.filters.coduri_critici?.map((c: string) => <span key={c} className="text-[10px] bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2 py-0.5 font-mono">{c}</span>)}
                          {s.filters.cpv_codes?.map((c: string) => <span key={c} className="text-[10px] bg-purple-50 text-purple-700 border border-purple-200 rounded-full px-2 py-0.5 font-mono">{c}</span>)}
                          {s.filters.search && <span className="text-[10px] bg-slate-100 text-slate-600 border border-slate-200 rounded-full px-2 py-0.5">"{s.filters.search}"</span>}
                        </div>
                        {/* Actions */}
                        <div className="flex items-center gap-2 mt-3 pt-2 border-t border-slate-100 flex-wrap">
                          <button
                            onClick={() => { setActiveScopeId(s.id); setShowScopeManager(false); }}
                            className={`text-xs font-medium transition ${activeScopeId === s.id ? 'text-blue-600' : 'text-slate-500 hover:text-blue-600'}`}
                          >
                            {activeScopeId === s.id ? 'Activ' : 'Activează'}
                          </button>
                          <span className="text-slate-200">|</span>
                          <button
                            onClick={() => {
                              // Load scope filters into Data Lake
                              setFilterRuling(s.filters.ruling ? s.filters.ruling.split(',') : []);
                              setFilterType(s.filters.tip_contestatie || '');
                              setFilterYears(s.filters.years ? s.filters.years.map(String) : []);
                              setFilterCritici(s.filters.coduri_critici || []);
                              setFilterCpv(s.filters.cpv_codes || []);
                              setFilterCategorie(s.filters.categorie || '');
                              setFilterClasa(s.filters.clasa || '');
                              setFileSearch(s.filters.search || '');
                              setFilterMotivRespingere(s.filters.motiv_respingere || []);
                              setFilterComplet(s.filters.complet || []);
                              setFilterDomeniu(s.filters.domeniu_legislativ || []);
                              setFilterTipProcedura(s.filters.tip_procedura || []);
                              setFilterCriteriuAtribuire(s.filters.criteriu_atribuire || []);
                              setFilterDateFrom(s.filters.data_decizie_from || '');
                              setFilterDateTo(s.filters.data_decizie_to || '');
                              setFilterValoareMin(s.filters.valoare_min || '');
                              setFilterValoareMax(s.filters.valoare_max || '');
                              setShowScopeManager(false);
                              setMode('datalake');
                            }}
                            className="text-xs text-slate-500 hover:text-teal-600 transition flex items-center gap-1"
                          >
                            <Filter size={11} /> Încarcă filtre
                          </button>
                          <span className="text-slate-200">|</span>
                          <button
                            onClick={() => setEditingScope({ id: s.id, name: s.name, description: s.description })}
                            className="text-xs text-slate-500 hover:text-amber-600 transition flex items-center gap-1"
                            title="Editează numele și descrierea"
                          >
                            <Pencil size={11} /> Editează
                          </button>
                          <span className="text-slate-200">|</span>
                          <button
                            onClick={() => {
                              // Load scope filters into Data Lake + enable filter editing mode
                              setFilterRuling(s.filters.ruling ? s.filters.ruling.split(',') : []);
                              setFilterType(s.filters.tip_contestatie || '');
                              setFilterYears(s.filters.years ? s.filters.years.map(String) : []);
                              setFilterCritici(s.filters.coduri_critici || []);
                              setFilterCpv(s.filters.cpv_codes || []);
                              setFilterCategorie(s.filters.categorie || '');
                              setFilterClasa(s.filters.clasa || '');
                              setFileSearch(s.filters.search || '');
                              setFilterMotivRespingere(s.filters.motiv_respingere || []);
                              setFilterComplet(s.filters.complet || []);
                              setFilterDomeniu(s.filters.domeniu_legislativ || []);
                              setFilterTipProcedura(s.filters.tip_procedura || []);
                              setFilterCriteriuAtribuire(s.filters.criteriu_atribuire || []);
                              setFilterDateFrom(s.filters.data_decizie_from || '');
                              setFilterDateTo(s.filters.data_decizie_to || '');
                              setFilterValoareMin(s.filters.valoare_min || '');
                              setFilterValoareMax(s.filters.valoare_max || '');
                              setEditingScopeFilters(s.id);
                              setShowScopeManager(false);
                              setMode('datalake');
                            }}
                            className="text-xs text-slate-500 hover:text-orange-600 transition flex items-center gap-1"
                            title="Modifică filtrele și re-salvează"
                          >
                            <Filter size={11} /> Modifică filtre
                          </button>
                          <span className="text-slate-200">|</span>
                          <button
                            onClick={() => {
                              if (confirm(`Șterge scope-ul "${s.name}"?`)) deleteScope(s.id);
                            }}
                            className="text-xs text-slate-500 hover:text-red-600 transition flex items-center gap-1"
                          >
                            <Trash2 size={11} /> Șterge
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Edit Decision Modal (admin) */}
      {editingDecision && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setEditingDecision(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-slate-800 mb-4 flex items-center gap-2">
              <Pencil size={20} className="text-amber-500" />
              Editare BO{editingDecision.an_bo}_{editingDecision.numar_bo}
            </h3>
            <div className="space-y-3 max-h-[60vh] overflow-y-auto">
              {[
                { key: 'solutie_contestatie', label: 'Soluție', placeholder: 'ADMIS / ADMIS_PARTIAL / RESPINS' },
                { key: 'motiv_respingere', label: 'Motiv respingere', placeholder: 'nefondată / tardivă / inadmisibilă / lipsită de interes / rămasă fără obiect' },
                { key: 'tip_contestatie', label: 'Tip contestație', placeholder: 'documentatie / rezultat' },
                { key: 'complet', label: 'Complet CNSC', placeholder: 'C1, C2, ... C11' },
                { key: 'contestator', label: 'Contestator', placeholder: 'Numele contestatorului' },
                { key: 'autoritate_contractanta', label: 'Autoritate contractantă', placeholder: 'Numele AC' },
                { key: 'cod_cpv', label: 'Cod CPV', placeholder: '45310000-3' },
                { key: 'cpv_descriere', label: 'Descriere CPV', placeholder: 'Descrierea codului CPV' },
              ].map(f => (
                <div key={f.key}>
                  <label className="block text-xs font-bold text-slate-600 uppercase mb-1">{f.label}</label>
                  <input
                    type="text"
                    className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500/40 outline-none"
                    placeholder={f.placeholder}
                    value={editDecisionForm[f.key] || ''}
                    onChange={(e) => setEditDecisionForm(prev => ({ ...prev, [f.key]: e.target.value }))}
                  />
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-3 mt-4">
              <button onClick={() => setEditingDecision(null)} className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800 font-medium">
                Anulează
              </button>
              <button
                onClick={async () => {
                  const externalId = `BO${editingDecision.an_bo}_${editingDecision.numar_bo}`;
                  // Build update payload: only non-empty changed fields
                  const payload: Record<string, any> = {};
                  for (const [k, v] of Object.entries(editDecisionForm)) {
                    if (v && v.trim()) payload[k] = v.trim();
                  }
                  if (Object.keys(payload).length === 0) {
                    setEditingDecision(null);
                    return;
                  }
                  try {
                    const res = await authFetch(`/api/v1/decisions/${externalId}`, {
                      method: 'PATCH',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify(payload),
                    });
                    if (res.ok) {
                      setEditingDecision(null);
                      fetchDecisions(apiDecisionsPage, fileSearch);
                    } else {
                      const data = await res.json().catch(() => ({}));
                      alert(`Eroare: ${data.detail || res.status}`);
                    }
                  } catch (err: any) {
                    alert(`Eroare: ${err.message}`);
                  }
                }}
                className="px-4 py-2 text-sm bg-amber-500 text-white rounded-lg hover:bg-amber-600 font-medium transition"
              >
                Salvează
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Import Decisions Modal */}
      {showImportModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setShowImportModal(false)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-slate-800 mb-2 flex items-center gap-2">
              <Upload size={20} className="text-indigo-600" />
              Import Decizii CNSC
            </h3>
            <p className="text-xs text-slate-500 mb-4">
              Importă decizii din fișiere <strong>.json</strong> (cu sau fără analiză) sau <strong>.txt</strong> (text brut, se parsează automat).
            </p>

            <div className="bg-slate-50 rounded-lg p-3 border border-slate-200 mb-4">
              <div className="text-xs font-bold text-slate-500 uppercase mb-2">Format JSON acceptat</div>
              <pre className="text-[10px] text-slate-600 overflow-x-auto whitespace-pre-wrap leading-relaxed">{`{
  "decisions": [{
    "filename": "BO2025_1234_R2_CPV_55520000-1_A.txt",
    "text_integral": "...",
    "numar_bo": 1234, "an_bo": 2025,
    "coduri_critici": ["R2"],
    "solutie_contestatie": "ADMIS",
    "argumentari": [{ "cod_critica": "R2", "argumentatie_cnsc": "...", "castigator_critica": "contestator" }]
  }]
}`}</pre>
            </div>

            <div className="mb-4">
              <input
                type="file"
                accept=".json,.txt"
                multiple
                onChange={async (e) => {
                  const files = e.target.files;
                  if (!files || files.length === 0) return;
                  setImportLoading(true);
                  setImportResult(null);

                  const formData = new FormData();
                  if (files.length === 1) {
                    formData.append('file', files[0]);
                  } else {
                    Array.from(files).forEach(f => formData.append('files', f));
                  }

                  try {
                    const endpoint = files.length === 1 ? '/api/v1/decisions/import' : '/api/v1/decisions/import/batch';
                    const token = localStorage.getItem('access_token');
                    const headers: Record<string, string> = {};
                    if (token) headers['Authorization'] = `Bearer ${token}`;
                    const res = await fetch(endpoint, { method: 'POST', headers, body: formData });
                    const data = await res.json();
                    if (!res.ok) {
                      setImportResult({ error: data.detail || `Eroare HTTP ${res.status}` });
                    } else {
                      setImportResult(data);
                      // Refresh Data Lake after import
                      if (data.imported > 0) {
                        fetchDecisions(1, fileSearch);
                        // Refresh stats
                        authFetch('/api/v1/decisions/stats/overview').then(r => r.ok ? r.json() : null).then(d => d && setDbStats(d)).catch(() => {});
                      }
                    }
                  } catch (err: any) {
                    setImportResult({ error: err.message || 'Eroare de rețea' });
                  } finally {
                    setImportLoading(false);
                    e.target.value = '';
                  }
                }}
                className="block w-full text-sm text-slate-600
                  file:mr-4 file:py-2 file:px-4
                  file:rounded-lg file:border-0
                  file:text-xs file:font-semibold
                  file:bg-indigo-50 file:text-indigo-700
                  hover:file:bg-indigo-100 file:cursor-pointer"
              />
              <p className="text-[10px] text-slate-400 mt-1">Poți selecta mai multe fișiere simultan (.json sau .txt)</p>
            </div>

            {importLoading && (
              <div className="flex items-center gap-2 text-indigo-600 text-sm mb-4">
                <Loader2 className="animate-spin" size={16} /> Se importă...
              </div>
            )}

            {importResult && !importResult.error && (
              <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 mb-4">
                <div className="flex items-center gap-2 text-emerald-700 font-semibold text-sm mb-2">
                  <CheckCircle size={16} /> Import finalizat
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div className="bg-white rounded p-2 border border-emerald-100">
                    <div className="text-lg font-bold text-emerald-700">{importResult.imported}</div>
                    <div className="text-[10px] text-emerald-600">Importate</div>
                  </div>
                  <div className="bg-white rounded p-2 border border-amber-100">
                    <div className="text-lg font-bold text-amber-600">{importResult.skipped}</div>
                    <div className="text-[10px] text-amber-500">Existente</div>
                  </div>
                  <div className="bg-white rounded p-2 border border-red-100">
                    <div className="text-lg font-bold text-red-600">{importResult.errors?.length || 0}</div>
                    <div className="text-[10px] text-red-500">Erori</div>
                  </div>
                </div>
                {importResult.errors?.length > 0 && (
                  <div className="mt-2 text-xs text-red-600 space-y-0.5 max-h-24 overflow-y-auto">
                    {importResult.errors.map((err: string, i: number) => (
                      <div key={i} className="flex items-start gap-1"><XCircle size={10} className="shrink-0 mt-0.5" /> {err}</div>
                    ))}
                  </div>
                )}
                {importResult.details?.length > 0 && (
                  <div className="mt-2 text-xs text-slate-600 space-y-0.5 max-h-24 overflow-y-auto">
                    {importResult.details.map((d: any, i: number) => (
                      <div key={i} className="flex items-center gap-1">
                        {d.status === 'imported' ? <CheckCircle size={10} className="text-emerald-500" /> : <span className="text-amber-500">~</span>}
                        <span className="font-mono">{d.external_id || d.filename}</span>
                        {d.has_analysis && <span className="text-emerald-500 text-[9px]">(+analiză: {d.analysis_count || '?'} critici)</span>}
                        {d.status === 'skipped' && <span className="text-amber-500 text-[9px]">(deja există)</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {importResult?.error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-red-700 text-sm flex items-start gap-2">
                <XCircle size={16} className="shrink-0 mt-0.5" /> {importResult.error}
              </div>
            )}

            <div className="flex justify-end">
              <button
                onClick={() => setShowImportModal(false)}
                className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800 font-medium"
              >
                Închide
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Save Scope Modal */}
      {showScopeModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setShowScopeModal(false)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-slate-800 mb-4 flex items-center gap-2">
              <Bookmark size={20} className="text-blue-600" />
              Salvează filtrele ca Scope
            </h3>
            <p className="text-xs text-slate-500 mb-4">
              Scope-ul salvat va putea fi utilizat în pagina Chat pentru a restricționa căutarea AI doar la deciziile filtrate.
            </p>

            <div className="space-y-3 mb-4">
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-1">Nume Scope *</label>
                <input
                  type="text"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500/40 outline-none"
                  placeholder="Ex: Catering ADMISE 2024"
                  value={scopeName}
                  onChange={(e) => setScopeName(e.target.value)}
                  maxLength={100}
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-1">Descriere (opțional)</label>
                <input
                  type="text"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500/40 outline-none"
                  placeholder="Ex: Decizii admise pe cod CPV catering"
                  value={scopeDescription}
                  onChange={(e) => setScopeDescription(e.target.value)}
                />
              </div>

              {/* Preview of current filters */}
              <div className="bg-slate-50 rounded-lg p-3 border border-slate-200">
                <div className="text-xs font-bold text-slate-500 uppercase mb-2">Filtre active</div>
                <div className="flex flex-wrap gap-1.5">
                  {filterRuling.map(r => <span key={r} className="text-[10px] bg-green-50 text-green-700 border border-green-200 rounded-full px-2 py-0.5">Soluție: {r === '__NULL__' ? 'Fără soluție' : r}</span>)}
                  {filterType && <span className="text-[10px] bg-purple-50 text-purple-700 border border-purple-200 rounded-full px-2 py-0.5">Tip: {filterType}</span>}
                  {filterYears.map(y => <span key={y} className="text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-2 py-0.5">An: {y}</span>)}
                  {filterCritici.map(c => <span key={c} className="text-[10px] bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2 py-0.5 font-mono">{c}</span>)}
                  {filterCpv.map(c => <span key={c} className="text-[10px] bg-purple-50 text-purple-700 border border-purple-200 rounded-full px-2 py-0.5 font-mono">{c}</span>)}
                  {filterCategorie && <span className="text-[10px] bg-orange-50 text-orange-700 border border-orange-200 rounded-full px-2 py-0.5">{filterCategorie}</span>}
                  {filterClasa && <span className="text-[10px] bg-teal-50 text-teal-700 border border-teal-200 rounded-full px-2 py-0.5">{filterClasa}</span>}
                  {fileSearch && <span className="text-[10px] bg-slate-100 text-slate-600 border border-slate-200 rounded-full px-2 py-0.5">"{fileSearch}"</span>}
                </div>
                <div className="text-xs text-slate-500 mt-2">{apiDecisionsTotal} decizii corespund filtrelor</div>
              </div>
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => setShowScopeModal(false)}
                className="flex-1 border border-slate-300 text-slate-600 py-2.5 rounded-lg text-sm font-medium hover:bg-slate-50 transition"
              >
                Anulează
              </button>
              <button
                onClick={async () => {
                  if (!scopeName.trim()) return;
                  try {
                    const filters: any = {};
                    if (filterRuling.length > 0) filters.ruling = filterRuling.join(',');
                    if (filterType) filters.tip_contestatie = filterType;
                    if (filterYears.length > 0) filters.years = filterYears.map(Number);
                    if (filterCritici.length > 0) filters.coduri_critici = filterCritici;
                    if (filterCpv.length > 0) filters.cpv_codes = filterCpv;
                    if (filterCategorie) filters.categorie = filterCategorie;
                    if (filterClasa) filters.clasa = filterClasa;
                    if (fileSearch.trim()) filters.search = fileSearch.trim();
                    if (filterMotivRespingere.length > 0) filters.motiv_respingere = filterMotivRespingere;
                    if (filterComplet.length > 0) filters.complet = filterComplet;
                    if (filterDomeniu.length > 0) filters.domeniu_legislativ = filterDomeniu;
                    if (filterTipProcedura.length > 0) filters.tip_procedura = filterTipProcedura;
                    if (filterCriteriuAtribuire.length > 0) filters.criteriu_atribuire = filterCriteriuAtribuire;
                    if (filterDateFrom) filters.data_decizie_from = filterDateFrom;
                    if (filterDateTo) filters.data_decizie_to = filterDateTo;
                    if (filterValoareMin) filters.valoare_min = filterValoareMin;
                    if (filterValoareMax) filters.valoare_max = filterValoareMax;

                    const res = await authFetch('/api/v1/scopes/', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        name: scopeName.trim(),
                        description: scopeDescription.trim() || null,
                        filters,
                      }),
                    });
                    if (res.ok) {
                      await fetchScopes();
                      setShowScopeModal(false);
                      setScopeName("");
                      setScopeDescription("");
                    } else {
                      const err = await res.json();
                      alert(err.detail || 'Eroare la salvare');
                    }
                  } catch (e) {
                    alert('Eroare de rețea');
                  }
                }}
                disabled={!scopeName.trim()}
                className="flex-1 bg-blue-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition disabled:opacity-50 flex items-center justify-center gap-1.5"
              >
                <Save size={14} /> Salvează
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Decision Viewer Modal */}
      {(viewingDecision || isLoadingDecision) && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => !isLoadingDecision && setViewingDecision(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            {isLoadingDecision ? (
              <div className="flex items-center justify-center p-20">
                <Loader2 size={32} className="animate-spin text-blue-600" />
                <span className="ml-3 text-slate-600">Se încarcă decizia...</span>
              </div>
            ) : viewingDecision && (() => {
              // Compute match count for search navigation
              const searchActive = decisionSearchTerm && decisionSearchTerm.length >= 2;
              const safeSearch = searchActive ? decisionSearchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : '';
              const matchCount = searchActive ? ((viewingDecision.content || "").match(new RegExp(safeSearch, 'gi')) || []).length : 0;

              return (
              <>
                {/* Header */}
                <div className="flex flex-col sm:flex-row items-start justify-between gap-3 sm:gap-4 p-4 md:p-6 border-b border-slate-200 shrink-0">
                  <div className="min-w-0">
                    <h2 className="text-lg md:text-xl font-bold text-slate-900 font-mono break-all">{viewingDecision.metadata?.case_number || viewingDecision.title}</h2>
                    <div className="flex gap-2 mt-2 flex-wrap">
                      {viewingDecision.metadata?.date && (
                        <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded border border-slate-200">
                          {new Date(viewingDecision.metadata.date).toLocaleDateString('ro-RO')}
                        </span>
                      )}
                      {viewingDecision.metadata?.ruling && (
                        <span className={`text-xs px-2 py-1 rounded border font-medium ${
                          viewingDecision.metadata.ruling === 'ADMIS' ? 'bg-green-50 text-green-700 border-green-200' :
                          viewingDecision.metadata.ruling === 'RESPINS' ? 'bg-red-50 text-red-700 border-red-200' :
                          'bg-yellow-50 text-yellow-700 border-yellow-200'
                        }`}>
                          {viewingDecision.metadata.ruling}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <div className="flex items-center border border-slate-200 rounded-lg overflow-hidden">
                      <div className="relative">
                        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                          type="text"
                          placeholder="Caută în decizie..."
                          value={decisionSearchTerm}
                          onChange={(e) => setDecisionSearchTerm(e.target.value)}
                          className="pl-8 pr-2 py-1.5 text-sm w-32 sm:w-44 focus:outline-none border-none"
                        />
                      </div>
                      {searchActive && (
                        <span className="text-xs text-slate-500 px-2 whitespace-nowrap tabular-nums">
                          {matchCount > 0 ? `${decisionSearchIndex + 1}/${matchCount}` : '0/0'}
                        </span>
                      )}
                      <button
                        onClick={() => { if (matchCount > 0) setDecisionSearchIndex(prev => prev <= 0 ? matchCount - 1 : prev - 1); }}
                        disabled={!searchActive || matchCount === 0}
                        className="p-1.5 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed border-l border-slate-200"
                      >
                        <ChevronUp size={14} className="text-slate-500" />
                      </button>
                      <button
                        onClick={() => { if (matchCount > 0) setDecisionSearchIndex(prev => prev >= matchCount - 1 ? 0 : prev + 1); }}
                        disabled={!searchActive || matchCount === 0}
                        className="p-1.5 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed border-l border-slate-200"
                      >
                        <ChevronDown size={14} className="text-slate-500" />
                      </button>
                      {decisionSearchTerm && (
                        <button
                          onClick={() => setDecisionSearchTerm("")}
                          className="p-1.5 hover:bg-slate-100 border-l border-slate-200"
                        >
                          <X size={14} className="text-slate-400" />
                        </button>
                      )}
                    </div>
                    <button
                      onClick={() => { setDecisionSearchTerm(""); setDecisionSearchIndex(0); setViewingDecision(null); }}
                      className="p-2 hover:bg-slate-100 rounded-lg transition text-slate-400 hover:text-slate-600 ml-1"
                    >
                      <X size={20} />
                    </button>
                  </div>
                </div>

                {/* Tab switcher */}
                <div className="flex items-center gap-1 px-6 pt-3 pb-0 shrink-0 border-b border-slate-200">
                  <button
                    onClick={() => setDecisionViewTab('raw')}
                    className={`px-4 py-2 text-xs font-medium rounded-t-lg border border-b-0 transition ${
                      decisionViewTab === 'raw'
                        ? 'bg-white text-slate-800 border-slate-200'
                        : 'bg-slate-50 text-slate-400 border-transparent hover:text-slate-600'
                    }`}
                  >
                    <FileText size={13} className="inline mr-1.5 -mt-0.5" />Text brut
                  </button>
                  <button
                    onClick={() => {
                      setDecisionViewTab('analysis');
                      if (!decisionAnalysis) {
                        const caseNum = viewingDecision.metadata?.case_number || viewingDecision.title;
                        fetchDecisionAnalysis(caseNum);
                      }
                    }}
                    className={`px-4 py-2 text-xs font-medium rounded-t-lg border border-b-0 transition ${
                      decisionViewTab === 'analysis'
                        ? 'bg-white text-slate-800 border-slate-200'
                        : 'bg-slate-50 text-slate-400 border-transparent hover:text-slate-600'
                    }`}
                  >
                    <BookOpen size={13} className="inline mr-1.5 -mt-0.5" />Analiză LLM
                  </button>
                </div>

                {/* Content */}
                {decisionViewTab === 'raw' ? (
                  <div className="flex-1 overflow-y-auto p-6" ref={decisionContentRef}>
                    <div
                      className="prose prose-slate max-w-none text-sm leading-relaxed whitespace-pre-wrap font-mono bg-slate-50 p-6 rounded-lg border border-slate-200"
                      dangerouslySetInnerHTML={{
                        __html: (() => {
                          const raw = viewingDecision.content || "";
                          const escaped = raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                          if (!searchActive) return escaped;
                          const regex = new RegExp(`(${safeSearch})`, 'gi');
                          let idx = 0;
                          return escaped.replace(regex, (match: string) => {
                            const isActive = idx === decisionSearchIndex;
                            const style = isActive
                              ? 'background:#fb923c;color:white;padding:1px 2px;border-radius:2px'
                              : 'background:#fef08a;padding:1px 2px;border-radius:2px';
                            return `<mark data-match="${idx++}" style="${style}">${match}</mark>`;
                          });
                        })()
                      }}
                    />
                  </div>
                ) : (
                  <div className="flex-1 overflow-y-auto p-6">
                    {isLoadingAnalysis ? (
                      <div className="flex items-center justify-center py-16">
                        <Loader2 size={24} className="animate-spin text-blue-500 mr-2" />
                        <span className="text-sm text-slate-500">Se încarcă analiza...</span>
                      </div>
                    ) : decisionAnalysis?.chunks?.length > 0 ? (
                      <div className="space-y-4">
                        {decisionAnalysis?.rezumat && (
                          <div className="bg-blue-50 rounded-lg border border-blue-200 p-5">
                            <h4 className="font-bold text-blue-800 text-sm mb-2">Rezumat</h4>
                            <p className="text-sm text-slate-700 leading-relaxed">{decisionAnalysis.rezumat}</p>
                          </div>
                        )}
                        {decisionAnalysis.chunks.map((chunk: any, i: number) => (
                          <div key={i} className="bg-slate-50 rounded-lg border border-slate-200 p-5">
                            {/* Chunk header */}
                            <div className="flex items-center gap-2 mb-3">
                              <span className="text-xs font-mono font-bold bg-blue-100 text-blue-700 px-2 py-0.5 rounded">
                                {chunk.cod_critica || `Critica ${i + 1}`}
                              </span>
                              {chunk.cod_critica && (CRITIQUE_LEGEND as any)[chunk.cod_critica] && (
                                <span className="text-xs text-slate-500">{(CRITIQUE_LEGEND as any)[chunk.cod_critica]}</span>
                              )}
                              {chunk.castigator_critica && chunk.castigator_critica !== 'unknown' && (
                                <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ml-auto ${
                                  chunk.castigator_critica === 'contestator' ? 'bg-emerald-100 text-emerald-700' :
                                  chunk.castigator_critica === 'autoritate' ? 'bg-red-100 text-red-700' :
                                  'bg-amber-100 text-amber-700'
                                }`}>
                                  {chunk.castigator_critica === 'contestator' ? 'Contestator' :
                                   chunk.castigator_critica === 'autoritate' ? 'Autoritate' : 'Parțial'}
                                </span>
                              )}
                            </div>
                            {/* Sections */}
                            <div className="space-y-3 text-xs leading-relaxed">
                              {chunk.argumente_contestator && (
                                <div>
                                  <h4 className="font-bold text-blue-700 mb-1">Argumente contestator</h4>
                                  <p className="text-slate-600 whitespace-pre-wrap">{chunk.argumente_contestator}</p>
                                  {chunk.jurisprudenta_contestator?.length > 0 && (
                                    <p className="text-slate-400 mt-1 italic">Jurisprudență: {chunk.jurisprudenta_contestator.join('; ')}</p>
                                  )}
                                </div>
                              )}
                              {chunk.argumente_ac && (
                                <div>
                                  <h4 className="font-bold text-orange-700 mb-1">Argumente AC</h4>
                                  <p className="text-slate-600 whitespace-pre-wrap">{chunk.argumente_ac}</p>
                                  {chunk.jurisprudenta_ac?.length > 0 && (
                                    <p className="text-slate-400 mt-1 italic">Jurisprudență: {chunk.jurisprudenta_ac.join('; ')}</p>
                                  )}
                                </div>
                              )}
                              {chunk.argumente_intervenienti?.length > 0 && chunk.argumente_intervenienti.map((interv: any, j: number) => (
                                <div key={j}>
                                  <h4 className="font-bold text-purple-700 mb-1">Intervenient #{interv.nr || j + 1}</h4>
                                  <p className="text-slate-600 whitespace-pre-wrap">{interv.argumente}</p>
                                </div>
                              ))}
                              {chunk.elemente_retinute_cnsc && (
                                <div>
                                  <h4 className="font-bold text-slate-700 mb-1">Elemente reținute CNSC</h4>
                                  <p className="text-slate-600 whitespace-pre-wrap">{chunk.elemente_retinute_cnsc}</p>
                                </div>
                              )}
                              {chunk.argumentatie_cnsc && (
                                <div>
                                  <h4 className="font-bold text-emerald-700 mb-1">Argumentație CNSC</h4>
                                  <p className="text-slate-600 whitespace-pre-wrap">{chunk.argumentatie_cnsc}</p>
                                  {chunk.jurisprudenta_cnsc?.length > 0 && (
                                    <p className="text-slate-400 mt-1 italic">Jurisprudență: {chunk.jurisprudenta_cnsc.join('; ')}</p>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-center py-16 text-slate-400">
                        <BookOpen size={32} className="mx-auto mb-3 text-slate-300" />
                        <p className="text-sm font-medium text-slate-500">Nu există analiză LLM</p>
                        <p className="text-xs mt-1">Această decizie nu a fost încă analizată cu LLM.</p>
                      </div>
                    )}
                  </div>
                )}

                {/* Footer */}
                <div className="p-4 border-t border-slate-200 flex justify-between items-center shrink-0">
                  <span className="text-xs text-slate-400">
                    {decisionViewTab === 'raw'
                      ? `${viewingDecision.content?.length?.toLocaleString()} caractere`
                      : `${decisionAnalysis?.chunks?.length || 0} critici analizate`
                    }
                  </span>
                  <button
                    onClick={() => { setDecisionSearchTerm(""); setDecisionSearchIndex(0); setViewingDecision(null); setDecisionAnalysis(null); }}
                    className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 transition text-sm font-medium"
                  >
                    Închide
                  </button>
                </div>
              </>
              );
            })()}
          </div>
        </div>
      )}

      {/* ANAP Speta viewer modal */}
      {(viewingSpeta || isLoadingSpeta) && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => !isLoadingSpeta && setViewingSpeta(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            {isLoadingSpeta ? (
              <div className="flex items-center justify-center p-20">
                <Loader2 size={32} className="animate-spin text-teal-600" />
                <span className="ml-3 text-slate-600">Se încarcă speța ANAP...</span>
              </div>
            ) : viewingSpeta && (
              <>
                {/* Header */}
                <div className="p-5 md:p-6 border-b border-slate-200 shrink-0">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h2 className="text-lg md:text-xl font-bold text-slate-900">Speță ANAP nr. {viewingSpeta.numar_speta}</h2>
                      <div className="flex gap-2 mt-2 flex-wrap">
                        <span className="text-xs bg-teal-50 text-teal-700 px-2 py-1 rounded border border-teal-200 font-medium">{viewingSpeta.categorie}</span>
                        {viewingSpeta.versiune > 1 && (
                          <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded border border-slate-200">v{viewingSpeta.versiune}</span>
                        )}
                        {viewingSpeta.data_publicarii && (
                          <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded border border-slate-200">
                            {new Date(viewingSpeta.data_publicarii).toLocaleDateString('ro-RO')}
                          </span>
                        )}
                      </div>
                    </div>
                    <button onClick={() => setViewingSpeta(null)} className="text-slate-400 hover:text-slate-700 p-1 shrink-0">
                      <X size={20} />
                    </button>
                  </div>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto p-5 md:p-6 space-y-5">
                  <div>
                    <h3 className="text-sm font-bold text-teal-700 uppercase tracking-wide mb-2">Întrebare</h3>
                    <div className="bg-teal-50 border border-teal-100 rounded-lg p-4 text-slate-800 leading-relaxed whitespace-pre-wrap text-sm">{viewingSpeta.intrebare}</div>
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-teal-700 uppercase tracking-wide mb-2">Răspuns ANAP</h3>
                    <div className="bg-white border border-slate-200 rounded-lg p-4 text-slate-800 leading-relaxed whitespace-pre-wrap text-sm">{viewingSpeta.raspuns}</div>
                  </div>
                </div>

                {/* Footer */}
                <div className="p-4 border-t border-slate-200 shrink-0 flex items-center justify-between">
                  <div className="flex gap-1.5 flex-wrap">
                    {viewingSpeta.taguri && viewingSpeta.taguri.length > 0 ? (
                      viewingSpeta.taguri.map((tag: string, i: number) => (
                        <span key={i} className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full border border-slate-200">{tag}</span>
                      ))
                    ) : (
                      <span className="text-xs text-slate-400">Fără taguri</span>
                    )}
                  </div>
                  <button onClick={() => setViewingSpeta(null)} className="bg-slate-100 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-slate-200 transition">Închide</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const root = createRoot(document.getElementById("root")!);
root.render(<App />);