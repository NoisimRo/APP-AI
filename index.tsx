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
} from "lucide-react";

// --- Types ---

type AppMode = 'dashboard' | 'datalake' | 'drafter' | 'redflags' | 'chat' | 'clarification' | 'rag' | 'training' | 'settings';

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
  const [drafterContext, setDrafterContext] = useState({ facts: "", authorityArgs: "", legalGrounds: "" });
  const [clarificationClause, setClarificationClause] = useState("");
  const [memoTopic, setMemoTopic] = useState("");

  // Red Flags States
  const [redFlagsText, setRedFlagsText] = useState("");
  const [redFlagsResults, setRedFlagsResults] = useState<any[]>([]);
  const [redFlagsTab, setRedFlagsTab] = useState<'manual' | 'upload'>('manual');
  const [uploadedDocDrafter, setUploadedDocDrafter] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocClarification, setUploadedDocClarification] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocRag, setUploadedDocRag] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocRedFlags, setUploadedDocRedFlags] = useState<{name: string, text: string} | null>(null);
  const [redFlagsProgress, setRedFlagsProgress] = useState("");

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
  const [uploadedDocTrainingContext, setUploadedDocTrainingContext] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocTrainingPlan, setUploadedDocTrainingPlan] = useState<{name: string, text: string} | null>(null);
  const [trainingBatchProgress, setTrainingBatchProgress] = useState<{current: number, total: number, results: string[]} | null>(null);

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

  // Mobile Sidebar State
  const [sidebarOpen, setSidebarOpen] = useState(false);

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

  // Register global handler for citation clicks
  useEffect(() => {
    (window as any).__openDecision = (decisionId: string) => {
      openDecision(decisionId);
    };
    return () => { delete (window as any).__openDecision; };
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
        const response = await fetch('/api/v1/decisions/stats/overview');
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
      const response = await fetch('/api/v1/settings/llm');
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
    fetchLLMSettings();
  }, []);

  // Fetch search scopes
  const fetchScopes = async () => {
    try {
      const res = await fetch('/api/v1/scopes/');
      if (res.ok) setScopes(await res.json());
    } catch (e) { console.error('Failed to fetch scopes:', e); }
  };

  useEffect(() => {
    fetchScopes();
  }, []);

  const deleteScope = async (id: string) => {
    try {
      const res = await fetch(`/api/v1/scopes/${id}`, { method: 'DELETE' });
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
      const res = await fetch(`/api/v1/scopes/${id}`, {
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
      const response = await fetch('/api/v1/settings/llm', {
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
      const response = await fetch('/api/v1/settings/llm/test', { method: 'POST' });
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
      const [critRes, cpvRes, catRes] = await Promise.all([
        fetch('/api/v1/decisions/filters/critici-codes'),
        fetch('/api/v1/decisions/filters/cpv-codes'),
        fetch('/api/v1/decisions/filters/categorii'),
      ]);
      if (critRes.ok) setCriticiOptions(await critRes.json());
      if (cpvRes.ok) setCpvOptions(await cpvRes.json());
      if (catRes.ok) setCategoriiOptions(await catRes.json());
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
  }, [fileSearch, filterRuling, filterType, filterYears, filterCritici, filterCpv, filterCategorie, filterClasa]);

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
          fetch('/api/v1/decisions/stats/cpv-top?limit=10'),
          fetch('/api/v1/decisions/stats/categorii'),
          fetch('/api/v1/decisions/stats/win-rate-by-category'),
          fetch('/api/v1/decisions/stats/win-rate-by-critici'),
          fetch('/api/v1/decisions/stats/cpv-top-grouped?limit=15'),
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
      await fetchStream(
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
            suffix += "\n\n📚 **Surse:** " + meta.citations.map((c: any) => `[[${c.decision_id}]]`).join(" ");
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
    }
  };

  const handleDrafting = async () => {
    setIsLoading(true);
    setStreamStatus("Se caută jurisprudență relevantă...");
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);

    try {
      await fetchStream(
        '/api/v1/drafter/stream',
        {
          facts: drafterContext.facts,
          authority_args: drafterContext.authorityArgs,
          legal_grounds: drafterContext.legalGrounds,
          scope_id: activeScopeId || undefined,
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
    }
  };

  const handleDocumentUpload = async (
    event: React.ChangeEvent<HTMLInputElement>,
    onTextExtracted?: (text: string) => void,
    setUploadedDoc?: (doc: {name: string, text: string}) => void,
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
      const response = await fetch('/api/v1/documents/analyze', {
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
      if (setUploadedDoc) {
        setUploadedDoc({ name: file.name, text: data.text });
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
    const textToAnalyze = redFlagsTab === 'upload' && uploadedDocRedFlags
      ? uploadedDocRedFlags.text
      : redFlagsText;

    if (!textToAnalyze || textToAnalyze.trim().length < 10) {
      alert("Introduceți text pentru analiză (min. 10 caractere) sau încărcați un document.");
      return;
    }

    setIsLoading(true);
    setRedFlagsResults([]);
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
      const response = await fetch('/api/v1/redflags/', {
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

  const handleClarification = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);
    try {
      const response = await fetch('/api/v1/clarification/', {
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

  const handleRAGMemo = async () => {
    if (!memoTopic || memoTopic.trim().length < 3) {
      alert("Introduceți un topic pentru memo juridic (min. 3 caractere).");
      return;
    }

    setIsLoading(true);
    setStreamStatus("Se caută jurisprudență relevantă...");
    setGeneratedContent("");

    try {
      await fetchStream(
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
    }
  };


  // --- Render Functions ---

  const MODE_LABELS: Record<AppMode, string> = {
    dashboard: 'Dashboard',
    datalake: 'Filtrare date',
    chat: 'Asistent AP',
    drafter: 'Drafter Contestații',
    redflags: 'Red Flags Detector',
    clarification: 'Clarificări',
    rag: 'Jurisprudență RAG',
    training: 'TrainingAP',
    settings: 'Setări LLM',
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
           <SidebarItem icon={Filter} label="Filtrare date" active={mode === 'datalake'} onClick={() => { setMode('datalake'); setSidebarOpen(false); }} badge={files.length} />
           <SidebarItem icon={LayoutDashboard} label="Dashboard" active={mode === 'dashboard'} onClick={() => { setMode('dashboard'); setSidebarOpen(false); }} />
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Instrumente Juridice</div>
           <SidebarItem icon={Scale} label="Drafter Contestații" active={mode === 'drafter'} onClick={() => { setMode('drafter'); setSidebarOpen(false); }} />
           <SidebarItem icon={AlertTriangle} label="Red Flags Detector" active={mode === 'redflags'} onClick={() => { setMode('redflags'); setSidebarOpen(false); }} />
           <SidebarItem icon={Search} label="Clarificări" active={mode === 'clarification'} onClick={() => { setMode('clarification'); setSidebarOpen(false); }} />
           <SidebarItem icon={BookOpen} label="Jurisprudență RAG" active={mode === 'rag'} onClick={() => { setMode('rag'); setSidebarOpen(false); }} />
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Formare</div>
           <SidebarItem icon={GraduationCap} label="TrainingAP" active={mode === 'training'} onClick={() => { setMode('training'); setSidebarOpen(false); }} />
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Sistem</div>
           <SidebarItem icon={Settings} label="Setări LLM" active={mode === 'settings'} onClick={() => { setMode('settings'); setSidebarOpen(false); }} />
        </div>
      </nav>

      <div className="p-4 border-t border-slate-800 bg-slate-900/50 cursor-pointer hover:bg-slate-800/50 transition-colors" onClick={() => { setMode('settings'); setSidebarOpen(false); }}>
         <div className="flex items-center gap-3">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold text-xs ${
              llmSettings?.active_provider === 'anthropic'
                ? 'bg-gradient-to-tr from-orange-500 to-amber-500'
                : llmSettings?.active_provider === 'groq'
                ? 'bg-gradient-to-tr from-purple-500 to-pink-500'
                : llmSettings?.active_provider === 'openai'
                ? 'bg-gradient-to-tr from-green-500 to-emerald-500'
                : llmSettings?.active_provider === 'openrouter'
                ? 'bg-gradient-to-tr from-rose-500 to-red-500'
                : 'bg-gradient-to-tr from-blue-500 to-purple-500'
            }`}>AI</div>
            <div>
               <p className="text-sm text-white font-medium">{
                 llmSettings?.active_model
                   ? llmSettings.active_model.replace(/-preview$/, '').replace(/^gemini-/, 'Gemini ').replace(/^claude-/, 'Claude ').replace(/-versatile$/, '').replace(/-instant$/, '').replace(/:free$/, ' ★')
                   : llmSettings?.active_provider === 'anthropic' ? 'Claude'
                   : llmSettings?.active_provider === 'groq' ? 'Groq'
                   : llmSettings?.active_provider === 'openai' ? 'OpenAI'
                   : llmSettings?.active_provider === 'openrouter' ? 'OpenRouter'
                   : 'Gemini'
               }</p>
               <p className={`text-xs ${llmSettings?.providers?.[llmSettings.active_provider]?.configured ? 'text-green-400' : 'text-yellow-400'}`}>
                 {llmSettings?.providers?.[llmSettings.active_provider]?.configured ? 'Operațional' : 'Neconfigurat'}
               </p>
            </div>
         </div>
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
          <h4 className="font-bold text-slate-800 text-sm">Filtrare date</h4>
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
                <Filter className="text-blue-600" size={22}/> Filtrare date
              </h2>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex items-center gap-1.5 bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full border border-emerald-200 text-[11px] font-medium">
                  <Wifi size={11} />
                  Conectat
                </div>
                <span className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-blue-50 text-blue-600 border border-blue-200">
                  PostgreSQL
                </span>
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
        <div className="px-4 md:px-6 py-2 border-b border-slate-200 bg-white shrink-0">
          <div className="flex items-center gap-2 flex-wrap">
              {/* Soluție Multi-select Dropdown */}
              <div className="relative flex-1 min-w-[100px]">
                <button
                  onClick={(e) => { e.stopPropagation(); setShowRulingDropdown(!showRulingDropdown); setShowYearDropdown(false); setShowCriticiDropdown(false); setShowCpvDropdown(false); setShowCategorieDropdown(false); setShowClasaDropdown(false); }}
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
                  onClick={(e) => { e.stopPropagation(); setShowCategorieDropdown(!showCategorieDropdown); setShowRulingDropdown(false); setShowYearDropdown(false); setShowCriticiDropdown(false); setShowCpvDropdown(false); setShowClasaDropdown(false); }}
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
                  onClick={(e) => { e.stopPropagation(); setShowClasaDropdown(!showClasaDropdown); setShowRulingDropdown(false); setShowYearDropdown(false); setShowCriticiDropdown(false); setShowCpvDropdown(false); setShowCategorieDropdown(false); }}
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
                  onClick={(e) => { e.stopPropagation(); setShowYearDropdown(!showYearDropdown); setShowRulingDropdown(false); setShowCriticiDropdown(false); setShowCpvDropdown(false); setShowCategorieDropdown(false); setShowClasaDropdown(false); }}
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
                  onClick={(e) => { e.stopPropagation(); setShowCriticiDropdown(!showCriticiDropdown); setShowRulingDropdown(false); setShowYearDropdown(false); setShowCpvDropdown(false); setShowCategorieDropdown(false); setShowClasaDropdown(false); }}
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
                  onClick={(e) => { e.stopPropagation(); setShowCpvDropdown(!showCpvDropdown); setShowRulingDropdown(false); setShowYearDropdown(false); setShowCriticiDropdown(false); setShowCategorieDropdown(false); setShowClasaDropdown(false); }}
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
              {(filterRuling.length > 0 || filterType || filterYears.length > 0 || fileSearch || filterCritici.length > 0 || filterCpv.length > 0 || filterCategorie || filterClasa) && (
                <button onClick={() => { setFilterRuling([]); setFilterType(""); setFilterYears([]); setFileSearch(""); setFilterCritici([]); setFilterCpv([]); setFilterCategorie(""); setFilterClasa(""); setEditingScopeFilters(null); }}
                  className="text-xs text-blue-600 hover:text-blue-800 font-medium whitespace-nowrap flex items-center gap-1 transition shrink-0">
                  <X size={13} /> Resetează
                </button>
              )}
              {/* Save scope button */}
              {(filterRuling.length > 0 || filterType || filterYears.length > 0 || filterCritici.length > 0 || filterCpv.length > 0 || filterCategorie || filterClasa || fileSearch) && (
                <button
                  onClick={() => setShowScopeModal(true)}
                  className="text-xs bg-blue-600 text-white hover:bg-blue-700 font-medium whitespace-nowrap flex items-center gap-1.5 transition px-3 py-1.5 rounded-lg shadow-sm shrink-0"
                >
                  <Bookmark size={13} /> Salvează
                </button>
              )}
              {/* Edit scope filters / manage scopes */}
              {scopes.length > 0 && (
                <button
                  onClick={() => setShowScopeManager(true)}
                  className="text-xs border border-slate-300 text-slate-600 hover:border-blue-400 hover:text-blue-600 font-medium whitespace-nowrap flex items-center gap-1.5 transition px-3 py-1.5 rounded-lg shrink-0"
                >
                  <Pencil size={12} /> Editează filtre
                </button>
              )}
            </div>
          {/* Active filter pills */}
          {(filterCritici.length > 0 || filterCpv.length > 0 || filterYears.length > 0 || filterCategorie || filterClasa) && (
            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
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
            </div>
          )}
        </div>

        {/* Scope filter editing banner */}
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
              <button
                onClick={() => setEditingScopeFilters(null)}
                className="text-amber-500 hover:text-amber-700"
              >
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

                  {/* Right action: Eye icon (opens analysis view) */}
                  <div
                    className="flex items-center justify-center px-5 border-l border-slate-100 group-hover:border-blue-100 transition-colors shrink-0"
                    onClick={(e) => {
                      e.stopPropagation();
                      openDecision(`BO${dec.an_bo}_${dec.numar_bo}`, 'analysis');
                    }}
                    title="Vezi analiza LLM"
                  >
                    <Eye size={22} className="text-slate-300 group-hover:text-blue-500 transition-colors" />
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
        <h2 className="text-lg font-bold text-slate-800 mb-4 flex gap-2 items-center">
          <Scale className="text-blue-600" size={20}/>
          Configurare Contestație
        </h2>
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
              onChange={(e) => handleDocumentUpload(e, (text) => setDrafterContext(prev => ({...prev, facts: text})), setUploadedDocDrafter)}
              className="block w-full text-sm text-slate-600
                file:mr-4 file:py-1.5 file:px-3
                file:rounded-lg file:border-0
                file:text-xs file:font-semibold
                file:bg-blue-50 file:text-blue-700
                hover:file:bg-blue-100"
            />
            {uploadedDocDrafter && (
              <p className="text-xs text-green-600 mt-2">
                ✓ {uploadedDocDrafter.name} ({uploadedDocDrafter.text.length} caractere)
              </p>
            )}
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Situația de Fapt</label>
            <textarea
              className={`w-full p-3 border rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.facts.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder="Descrie cronologia evenimentelor sau încarcă un document..."
              value={drafterContext.facts}
              onChange={(e) => setDrafterContext({...drafterContext, facts: e.target.value})}
            />
            <CharCounter value={drafterContext.facts} maxLength={200000} />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Argumentele Autorității</label>
            <textarea
              className={`w-full p-3 border rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.authorityArgs.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder="Ce motive a invocat autoritatea pentru respingere?"
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
          
          <button 
            onClick={handleDrafting}
            disabled={isLoading}
            className="w-full bg-slate-900 text-white py-4 rounded-xl font-medium hover:bg-slate-800 transition flex justify-center items-center gap-2 shadow-lg hover:shadow-xl mt-4"
          >
            {isLoading ? <><Loader2 className="animate-spin" size={18} /> <span className="text-sm">{streamStatus || "Se procesează..."}</span></> : "Generează Proiect"}
          </button>
        </div>
      </div>
      
      <div className="w-full md:w-2/3 p-4 md:p-10 overflow-y-auto bg-white">
        {generatedContent ? (
          <div className="max-w-3xl mx-auto">
             <div className="flex justify-end mb-4">
                <button className="text-sm text-blue-600 font-medium hover:underline">Descarcă .DOCX</button>
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

      await fetchStream(
        '/api/v1/training/generate/stream',
        {
          ...buildTrainingRequestBody('program_formare'),
          selected_types: trainingSelectedTypes.length > 0 ? trainingSelectedTypes : undefined,
        },
        (text) => setTrainingResult(prev => prev + text),
        (meta) => { setTrainingMeta(meta); setTrainingLoading(false); },
        (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); setTrainingLoading(false); },
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

        await fetchStream(
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
      return;
    }

    // Individual mode (default)
    resetUI();

    await fetchStream(
      '/api/v1/training/generate/stream',
      buildTrainingRequestBody(),
      (text) => setTrainingResult(prev => prev + text),
      (meta) => { setTrainingMeta(meta); setTrainingLoading(false); },
      (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); setTrainingLoading(false); },
    );
  };

  const handleTrainingExport = async (format: 'docx' | 'pdf' | 'md') => {
    const contentToExport = trainingEditedResult ?? trainingResult;
    if (!contentToExport) return;
    try {
      const response = await fetch('/api/v1/training/export', {
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

          <div className="space-y-5">
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
                    onChange={(e) => handleDocumentUpload(e, (text) => setTrainingProgramPlan(text), setUploadedDocTrainingPlan)}
                    className="block w-full text-sm text-slate-600 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-amber-50 file:text-amber-700 hover:file:bg-amber-100"
                  />
                  {uploadedDocTrainingPlan && (
                    <p className="text-xs text-green-600 mt-1">
                      ✓ {uploadedDocTrainingPlan.name} ({uploadedDocTrainingPlan.text.length.toLocaleString()} car.)
                    </p>
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
                      onChange={(e) => handleDocumentUpload(e, (text) => setTrainingContext(text), setUploadedDocTrainingContext)}
                      className="block w-full text-sm text-slate-600 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-amber-50 file:text-amber-700 hover:file:bg-amber-100"
                    />
                    {uploadedDocTrainingContext && (
                      <p className="text-xs text-green-600 mt-1">
                        ✓ {uploadedDocTrainingContext.name} ({uploadedDocTrainingContext.text.length.toLocaleString()} car.)
                      </p>
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
           <span className="text-xs text-slate-500 bg-slate-100 px-2 py-1 rounded">Conectat la baza de date CNSC</span>
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
      <main className="flex-1 overflow-hidden relative shadow-2xl z-10 md:rounded-l-2xl md:border-l border-slate-200/50 bg-white md:ml-[-1px] pt-[52px] md:pt-0 flex flex-col">
        <div className="flex-1 overflow-hidden flex flex-col">
        {mode === 'dashboard' && renderDashboard()}
        {mode === 'datalake' && renderDataLake()}
        {mode === 'drafter' && renderDrafter()}
        {mode === 'chat' && renderChat()}
        {mode === 'redflags' && (
          <div className="h-full flex flex-col md:flex-row bg-white">
            {/* Left panel — input */}
            <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
              <h2 className="text-lg font-bold text-slate-800 mb-2 flex gap-2 items-center">
                <AlertTriangle className="text-red-500" size={20}/> Red Flags Detector
              </h2>
              <p className="text-xs text-slate-500 mb-6">Identifică clauze restrictive în documentația de achiziții publice.</p>

              {/* Tabs */}
              <div className="flex gap-1 mb-4 border-b border-slate-200">
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
                      onChange={(e) => handleDocumentUpload(e, (text) => setRedFlagsText(text), setUploadedDocRedFlags)}
                      className="block w-full text-sm text-slate-600
                        file:mr-4 file:py-1.5 file:px-3
                        file:rounded-lg file:border-0
                        file:text-xs file:font-semibold
                        file:bg-red-50 file:text-red-700
                        hover:file:bg-red-100"
                    />
                    {uploadedDocRedFlags && (
                      <p className="text-xs text-green-600 mt-2">
                        ✓ {uploadedDocRedFlags.name} ({uploadedDocRedFlags.text.length} caractere)
                      </p>
                    )}
                  </div>
                  <button
                    onClick={handleRedFlags}
                    disabled={isLoading || !uploadedDocRedFlags}
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
                  <div className="bg-white p-4 rounded-lg border border-slate-200 flex items-center justify-between sticky top-0 z-10">
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
                                <span
                                  key={ref}
                                  className="text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded border border-blue-200 font-mono"
                                >
                                  {ref}
                                </span>
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
                      </div>
                    </div>
                  ))}
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
              <h2 className="text-lg font-bold text-slate-800 mb-6 flex gap-2 items-center">
                <Search className="text-purple-600" size={20}/> Asistent Clarificări
              </h2>
              <div className="space-y-4">
                <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
                  <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
                    Încarcă document (.txt, .md, .pdf)
                  </label>
                  <input
                    type="file"
                    accept=".txt,.md,.pdf,.doc,.docx"
                    onChange={(e) => handleDocumentUpload(e, (text) => setClarificationClause(text), setUploadedDocClarification)}
                    className="block w-full text-sm text-slate-600
                      file:mr-4 file:py-1.5 file:px-3
                      file:rounded-lg file:border-0
                      file:text-xs file:font-semibold
                      file:bg-purple-50 file:text-purple-700
                      hover:file:bg-purple-100"
                  />
                  {uploadedDocClarification && (
                    <p className="text-xs text-green-600 mt-2">
                      ✓ {uploadedDocClarification.name} ({uploadedDocClarification.text.length} caractere)
                    </p>
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
              <header className="mb-4">
                 <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2"><BookOpen className="text-teal-600"/> Jurisprudență RAG</h2>
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
                           onChange={(e) => handleDocumentUpload(e, (text) => setMemoTopic(text), setUploadedDocRag)}
                           className="block w-full text-sm text-slate-600
                             file:mr-4 file:py-1.5 file:px-3
                             file:rounded-lg file:border-0
                             file:text-xs file:font-semibold
                             file:bg-teal-50 file:text-teal-700
                             hover:file:bg-teal-100"
                         />
                         {uploadedDocRag && (
                           <p className="text-xs text-green-600 mt-2">
                             ✓ {uploadedDocRag.name} ({uploadedDocRag.text.length} caractere)
                           </p>
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
                       <div className="prose prose-slate max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(generatedContent) }} />
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
        {mode === 'training' && renderTraining()}
        {mode === 'settings' && renderSettings()}
        </div>

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
                <p className="text-xs mt-1">Mergi pe pagina Filtrare date, aplică filtre, apoi apasă "Salvează Scope".</p>
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

                    const res = await fetch('/api/v1/scopes/', {
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
    </div>
  );
};

const root = createRoot(document.getElementById("root")!);
root.render(<App />);