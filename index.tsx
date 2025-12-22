import React, { useState, useRef, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { GoogleGenAI, Type } from "@google/genai";
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
  ChevronRight,
  BookOpen,
  Send,
  Loader2,
  Trash2
} from "lucide-react";

// --- Types ---

type AppMode = 'dashboard' | 'drafter' | 'redflags' | 'chat' | 'clarification' | 'rag';

interface UploadedFile {
  name: string;
  type: string;
  content: string; // Base64
  metadata?: {
    year?: string;
    bulletin?: string;
    critics?: string[];
    cpv?: string;
    ruling?: 'Admis' | 'Respins' | 'Unknown';
  };
}

interface Message {
  role: 'user' | 'model';
  text: string;
}

// --- Helper Functions ---

const parseFilenameMetadata = (filename: string) => {
  // Expected: BO2025 - [Bulletin] - [Critics] - [CPV] - [A/R].txt/pdf
  // This is a heuristic parser based on user description
  const parts = filename.split(/[-_]/);
  const metadata: UploadedFile['metadata'] = { ruling: 'Unknown' };

  if (filename.includes('BO2025') || filename.includes('BO2024')) {
    metadata.year = filename.substring(2, 6);
  }

  // Detect Ruling at the end
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
      // Remove data URL prefix (e.g., "data:application/pdf;base64,")
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = error => reject(error);
  });
};

// --- Components ---

const SidebarItem = ({ 
  icon: Icon, 
  label, 
  active, 
  onClick 
}: { 
  icon: any, 
  label: string, 
  active: boolean, 
  onClick: () => void 
}) => (
  <button 
    onClick={onClick}
    className={`w-full flex items-center gap-3 px-4 py-3 text-sm font-medium transition-colors ${
      active 
        ? "bg-slate-800 text-white border-l-4 border-blue-500" 
        : "text-slate-400 hover:bg-slate-800/50 hover:text-slate-200"
    }`}
  >
    <Icon size={18} />
    {label}
  </button>
);

// --- Main Application ---

const App = () => {
  const [mode, setMode] = useState<AppMode>('dashboard');
  const [apiKey] = useState(process.env.API_KEY || "");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  
  // Chat/Interaction States
  const [chatMessages, setChatMessages] = useState<Message[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [generatedContent, setGeneratedContent] = useState<string>("");

  // Specialized Input States
  const [drafterContext, setDrafterContext] = useState({ facts: "", authorityArgs: "", legalGrounds: "" });
  const [clarificationClause, setClarificationClause] = useState("");
  const [memoTopic, setMemoTopic] = useState("");

  const chatEndRef = useRef<HTMLDivElement>(null);

  const ai = new GoogleGenAI({ apiKey });

  // Scroll to bottom of chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, generatedContent]);

  // Clear generated content when switching modes
  useEffect(() => {
    setGeneratedContent("");
  }, [mode]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const newFiles: UploadedFile[] = [];
      for (let i = 0; i < e.target.files.length; i++) {
        const file = e.target.files[i];
        const base64 = await fileToBase64(file);
        newFiles.push({
          name: file.name,
          type: file.type,
          content: base64,
          metadata: parseFilenameMetadata(file.name)
        });
      }
      setFiles(prev => [...prev, ...newFiles]);
    }
  };

  const removeFile = (index: number) => {
    setFiles(files.filter((_, i) => i !== index));
  };

  // --- API Interaction Handlers ---

  const handleChat = async () => {
    if (!chatInput.trim()) return;
    const userMsg = chatInput;
    setChatMessages(prev => [...prev, { role: 'user', text: userMsg }]);
    setChatInput("");
    setIsLoading(true);

    try {
      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: [
          ...chatMessages.map(m => ({ role: m.role, parts: [{ text: m.text }] })),
          { role: 'user', parts: [{ text: userMsg }] }
        ],
        config: {
          systemInstruction: "Ești un expert în achiziții publice din România (Legea 98/2016, Legea 101/2016). Răspunde concis, profesionist și citează articole relevante din lege."
        }
      });
      
      setChatMessages(prev => [...prev, { role: 'model', text: response.text || "" }]);
    } catch (err) {
      setChatMessages(prev => [...prev, { role: 'model', text: "Eroare la procesarea cererii." }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDrafting = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    
    try {
      const prompt = `
        Ești un avocat expert în achiziții publice. Redactează o contestație către CNSC (Consiliul Național de Soluționare a Contestațiilor).
        
        Detalii faptice (Istoric): ${drafterContext.facts}
        Argumentele Autorității (de combătut): ${drafterContext.authorityArgs}
        Temei legal invocat: ${drafterContext.legalGrounds}
        
        Structura obligatorie:
        1. Identificarea părților.
        2. Situația de fapt (rezumat).
        3. Motivele contestației (Argumente de fapt și de drept). Analiza critică a deciziei autorității.
        4. Suspendarea procedurii (dacă e cazul).
        5. Dispozitiv (Solicitări finale: Anulare raport, Reevaluare, etc.).
        
        Folosește un limbaj juridic formal, persuasiv.
      `;

      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: prompt
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      setGeneratedContent("A apărut o eroare la generare. Verificați cheia API.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleRedFlags = async () => {
    if (files.length === 0) {
      alert("Te rog încarcă fișierul (Caiet de Sarcini/Fișa de Date) pentru analiză.");
      return;
    }
    setIsLoading(true);
    setGeneratedContent("");

    try {
      // Prepare parts with file data. We construct the array literal to allow both inlineData and text parts.
      const parts = [
        ...files.map(f => ({
          inlineData: { mimeType: f.type || 'text/plain', data: f.content }
        })),
        {
          text: `
        Analizează documentele atașate (Documentație de Atribuire). 
        Identifică "Steaguri Roșii" (Red Flags) - clauze care ar putea fi restrictive, abuzive sau care încalcă principiile tratamentului egal.
        Pentru fiecare problemă identificată:
        1. Citează clauza.
        2. Explică riscul (de ce e restrictivă?).
        3. Propune o "Imunizare": Ce clarificare să trimită operatorul economic pentru a elimina clauza fără a depune încă contestație?
      ` }
      ];

      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: { parts }
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      setGeneratedContent("Eroare la analiza documentelor.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleClarification = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    try {
      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: `
          Acționezi ca un consultant în achiziții. 
          Un client a găsit această clauză problematică în documentație: "${clarificationClause}".
          
          Redactează o Cerere de Clarificare oficială către autoritatea contractantă.
          Tonul trebuie să fie respectuos, dar ferm, invocând legislația (Legea 98/2016) pentru a demonstra că cerința limitează artificial concurența.
          Scopul este eliminarea sau modificarea cerinței.
        `
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      setGeneratedContent("Eroare la generare.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleRAGMemo = async () => {
    if (files.length === 0) {
      alert("Încarcă fișiere de jurisprudență relevantă (Ex: Decizii CNSC similare).");
      return;
    }
    setIsLoading(true);
    setGeneratedContent("");

    try {
      const parts = [
        ...files.map(f => ({
          inlineData: { mimeType: f.type || 'text/plain', data: f.content }
        })),
        { text: `
        Folosind DOAR fișierele atașate (Decizii CNSC) ca bază de jurisprudență:
        Redactează un Memo Juridic pe tema: "${memoTopic}".
        
        Structura Memo:
        1. Sinteza practicii CNSC din fișierele atașate (cum s-a pronunțat consiliul în cazuri similare?).
        2. Extrage argumentele câștigătoare (din secțiunea 'motivare_cnsc' a fișierelor).
        3. Concluzie: Care sunt șansele de câștig pe speța actuală bazat pe aceste precedente?
      `}
      ];

      // Using gemini-3-pro-preview for complex reasoning over multiple files
      const response = await ai.models.generateContent({
        model: 'gemini-3-pro-preview',
        contents: { parts },
        config: {
           // Providing higher budget for deep analysis of multiple PDF/TXT files
           thinkingConfig: { thinkingBudget: 2048 } 
        }
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      console.error(err);
      setGeneratedContent("Eroare la generarea memo-ului. Asigură-te că fișierele sunt text sau PDF valid.");
    } finally {
      setIsLoading(false);
    }
  };


  // --- Render Functions ---

  const renderSidebar = () => (
    <div className="w-64 bg-slate-900 h-screen flex flex-col border-r border-slate-800 shrink-0">
      <div className="p-6 border-b border-slate-800">
        <h1 className="text-xl font-bold text-white flex items-center gap-2">
          <Gavel className="text-blue-500" />
          CNSC Expert
        </h1>
        <p className="text-xs text-slate-500 mt-1">Asistent Achiziții Publice</p>
      </div>
      <nav className="flex-1 overflow-y-auto py-4">
        <div className="px-4 mb-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">General</div>
        <SidebarItem icon={FileText} label="Context & Fișiere" active={mode === 'dashboard'} onClick={() => setMode('dashboard')} />
        <SidebarItem icon={MessageSquare} label="Chatbot Legislativ" active={mode === 'chat'} onClick={() => setMode('chat')} />
        
        <div className="px-4 mt-6 mb-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">Litigii</div>
        <SidebarItem icon={Scale} label="Redactare Contestații" active={mode === 'drafter'} onClick={() => setMode('drafter')} />
        <SidebarItem icon={AlertTriangle} label="Red Flags & Imunizare" active={mode === 'redflags'} onClick={() => setMode('redflags')} />
        <SidebarItem icon={Search} label="Clarificări" active={mode === 'clarification'} onClick={() => setMode('clarification')} />
        <SidebarItem icon={BookOpen} label="Jurisprudență (RAG)" active={mode === 'rag'} onClick={() => setMode('rag')} />
      </nav>
      <div className="p-4 border-t border-slate-800">
         <div className="text-xs text-slate-500 mb-2">Powered by Gemini 2.5</div>
      </div>
    </div>
  );

  const renderFileContext = () => (
    <div className="p-8 max-w-4xl mx-auto">
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
        <h2 className="text-xl font-bold text-slate-800 mb-4 flex items-center gap-2">
          <Upload className="text-blue-600" />
          Manager de Context
        </h2>
        <p className="text-slate-600 mb-6">
          Încarcă deciziile CNSC (.txt, .pdf) sau documentația de atribuire aici. Aceste fișiere vor fi utilizate de modulele "Red Flags" și "Jurisprudență".
        </p>

        <label className="block w-full border-2 border-dashed border-slate-300 rounded-lg p-8 text-center hover:bg-slate-50 transition cursor-pointer">
          <input type="file" multiple className="hidden" onChange={handleFileUpload} accept=".txt,.pdf" />
          <div className="flex flex-col items-center">
            <Upload size={32} className="text-slate-400 mb-2" />
            <span className="text-slate-700 font-medium">Click pentru a încărca fișiere</span>
            <span className="text-xs text-slate-500 mt-1">Acceptă format .txt și .pdf</span>
          </div>
        </label>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {files.map((file, idx) => (
          <div key={idx} className="bg-white p-4 rounded-lg border border-slate-200 flex items-start justify-between shadow-sm">
            <div className="flex items-start gap-3">
              <div className="p-2 bg-blue-50 text-blue-600 rounded">
                <FileText size={20} />
              </div>
              <div>
                <p className="font-medium text-slate-800 text-sm truncate w-48" title={file.name}>{file.name}</p>
                <div className="flex gap-2 mt-1 flex-wrap">
                  {file.metadata?.year && <span className="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-600">{file.metadata.year}</span>}
                  {file.metadata?.ruling === 'Admis' && <span className="text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded flex items-center gap-1"><CheckCircle size={10}/> Admis</span>}
                  {file.metadata?.ruling === 'Respins' && <span className="text-[10px] bg-red-100 text-red-700 px-1.5 py-0.5 rounded flex items-center gap-1"><XCircle size={10}/> Respins</span>}
                </div>
              </div>
            </div>
            <button onClick={() => removeFile(idx)} className="text-slate-400 hover:text-red-500">
              <Trash2 size={16} />
            </button>
          </div>
        ))}
      </div>
      
      {files.length > 0 && (
         <div className="mt-6 p-4 bg-blue-50 text-blue-800 rounded-md text-sm border border-blue-100">
           <span className="font-bold">Info:</span> Ai încărcat {files.length} fișiere. Folosește modulul <strong>Red Flags</strong> pentru analiză sau <strong>Jurisprudență</strong> pentru sinteză.
         </div>
      )}
    </div>
  );

  const renderDrafter = () => (
    <div className="h-full flex flex-col md:flex-row">
      <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50">
        <h2 className="text-lg font-bold text-slate-800 mb-4 flex gap-2 items-center"><Scale size={20}/> Configurare Contestație</h2>
        
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">Situația de Fapt (Istoric)</label>
            <textarea 
              className="w-full p-3 border border-slate-300 rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none"
              placeholder="Ex: Am depus oferta în data X, am fost descalificați pe motiv că..."
              value={drafterContext.facts}
              onChange={(e) => setDrafterContext({...drafterContext, facts: e.target.value})}
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">Argumentele Autorității</label>
            <textarea 
              className="w-full p-3 border border-slate-300 rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none"
              placeholder="Ce scrie în comunicarea rezultatului? De ce v-au respins?"
              value={drafterContext.authorityArgs}
              onChange={(e) => setDrafterContext({...drafterContext, authorityArgs: e.target.value})}
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-500 uppercase mb-1">Temei Legal (Articole)</label>
            <input 
              type="text"
              className="w-full p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
              placeholder="Ex: Art. 215 din Legea 98/2016"
              value={drafterContext.legalGrounds}
              onChange={(e) => setDrafterContext({...drafterContext, legalGrounds: e.target.value})}
            />
          </div>
          
          <button 
            onClick={handleDrafting}
            disabled={isLoading}
            className="w-full bg-blue-600 text-white py-3 rounded-lg font-medium hover:bg-blue-700 transition flex justify-center items-center gap-2"
          >
            {isLoading ? <Loader2 className="animate-spin" /> : "Generează Proiect Contestație"}
          </button>
        </div>
      </div>
      
      <div className="w-full md:w-2/3 p-8 overflow-y-auto bg-white">
        {generatedContent ? (
          <div className="prose max-w-none font-serif text-slate-800 leading-relaxed whitespace-pre-wrap">
             <h3 className="text-center font-bold text-xl mb-8 uppercase">Contestație</h3>
             {generatedContent}
          </div>
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-slate-400">
            <Scale size={48} className="mb-4 opacity-20" />
            <p>Completează detaliile în stânga și apasă pe Generare.</p>
          </div>
        )}
      </div>
    </div>
  );

  const renderRedFlags = () => (
    <div className="p-8 max-w-5xl mx-auto h-full overflow-y-auto">
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-slate-800 mb-2 flex items-center gap-2">
          <AlertTriangle className="text-red-500" />
          Detector de "Steaguri Roșii"
        </h2>
        <p className="text-slate-600">
          Analizează Caietul de Sarcini pentru clauze restrictive. Asigură-te că ai încărcat documentația în secțiunea "Context".
        </p>
      </div>

      {files.length === 0 ? (
        <div className="bg-amber-50 border border-amber-200 p-4 rounded-lg text-amber-800 flex items-center gap-2">
          <AlertTriangle size={20} />
          Nu sunt fișiere încărcate. Mergi la tab-ul "Context & Fișiere" pentru a încărca documentația.
        </div>
      ) : (
        <button 
          onClick={handleRedFlags}
          disabled={isLoading}
          className="bg-red-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-red-700 transition flex items-center gap-2 mb-8"
        >
          {isLoading ? <Loader2 className="animate-spin" /> : "Analizează Documentația Încărcată"}
        </button>
      )}

      {generatedContent && (
        <div className="bg-white border border-slate-200 rounded-xl p-8 shadow-sm whitespace-pre-wrap font-mono text-sm">
          {generatedContent}
        </div>
      )}
    </div>
  );

  const renderChat = () => (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-6 space-y-4 bg-slate-50">
        {chatMessages.length === 0 && (
           <div className="text-center text-slate-400 mt-20">
             <MessageSquare size={48} className="mx-auto mb-4 opacity-20" />
             <p>Întreabă orice despre legislația achizițiilor publice.</p>
           </div>
        )}
        {chatMessages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-2xl p-4 ${
              msg.role === 'user' 
                ? 'bg-blue-600 text-white rounded-br-none' 
                : 'bg-white border border-slate-200 text-slate-800 rounded-bl-none shadow-sm'
            }`}>
              {msg.text}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex justify-start">
             <div className="bg-white border border-slate-200 p-4 rounded-2xl rounded-bl-none shadow-sm flex gap-2 items-center">
               <Loader2 size={16} className="animate-spin text-blue-600" />
               <span className="text-sm text-slate-500">Se gândește...</span>
             </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>
      <div className="p-4 bg-white border-t border-slate-200">
        <div className="flex gap-2 max-w-4xl mx-auto">
          <input 
            type="text" 
            className="flex-1 border border-slate-300 rounded-lg px-4 py-3 focus:ring-2 focus:ring-blue-500 outline-none"
            placeholder="Scrie mesajul tău..."
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleChat()}
          />
          <button 
            onClick={handleChat}
            disabled={isLoading || !chatInput.trim()}
            className="bg-blue-600 text-white px-6 rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            <Send size={20} />
          </button>
        </div>
      </div>
    </div>
  );

  const renderClarification = () => (
    <div className="p-8 max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold text-slate-800 mb-6 flex items-center gap-2">
        <Search className="text-purple-600" />
        Asistent Clarificări
      </h2>
      
      <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200 mb-6">
        <label className="block font-medium text-slate-700 mb-2">Clauza problematică / neclară:</label>
        <textarea 
          className="w-full p-4 border border-slate-300 rounded-lg h-32 mb-4 focus:ring-2 focus:ring-purple-500 outline-none"
          placeholder="Lipește aici textul din fișa de date care necesită clarificare..."
          value={clarificationClause}
          onChange={(e) => setClarificationClause(e.target.value)}
        />
        <button 
          onClick={handleClarification}
          disabled={isLoading || !clarificationClause}
          className="bg-purple-600 text-white px-6 py-2 rounded-lg font-medium hover:bg-purple-700 transition w-full"
        >
          {isLoading ? "Se redactează..." : "Redactează Cerere de Clarificare"}
        </button>
      </div>

      {generatedContent && (
        <div className="bg-white border border-slate-200 rounded-xl p-8 shadow-sm">
           <h3 className="font-bold text-lg mb-4 text-slate-800">Proiect Cerere:</h3>
           <div className="prose max-w-none whitespace-pre-wrap text-slate-700">
             {generatedContent}
           </div>
        </div>
      )}
    </div>
  );

  const renderRAG = () => (
    <div className="h-full flex flex-col p-6 overflow-hidden">
      <div className="mb-6 shrink-0">
        <h2 className="text-2xl font-bold text-slate-800 mb-2 flex items-center gap-2">
          <BookOpen className="text-teal-600" />
          Jurisprudență & Memo-uri (RAG)
        </h2>
        <p className="text-slate-600 text-sm">
          Generează sinteze juridice bazate pe deciziile CNSC încărcate în secțiunea Context.
        </p>
      </div>
      
      <div className="flex-1 flex gap-6 overflow-hidden">
        <div className="w-1/3 flex flex-col gap-4">
           <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex-1 flex flex-col">
              <h3 className="font-semibold text-slate-800 mb-3">Fișiere Jurisprudență ({files.length})</h3>
              <div className="flex-1 overflow-y-auto space-y-2 mb-4 border rounded p-2 bg-slate-50">
                {files.map((f, i) => (
                  <div key={i} className="text-xs p-2 bg-white border border-slate-200 rounded text-slate-600 truncate">
                    {f.name}
                  </div>
                ))}
                {files.length === 0 && <div className="text-xs text-slate-400 text-center mt-4">Niciun fișier. Încarcă decizii în Dashboard.</div>}
              </div>
              
              <label className="text-sm font-medium text-slate-700">Subiect Memo:</label>
              <input 
                type="text"
                className="w-full border border-slate-300 rounded p-2 text-sm mb-3 mt-1"
                placeholder="Ex: Respingere ofertă preț neobișnuit de scăzut"
                value={memoTopic}
                onChange={(e) => setMemoTopic(e.target.value)}
              />
              <button 
                onClick={handleRAGMemo}
                disabled={isLoading || files.length === 0}
                className="w-full bg-teal-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-teal-700"
              >
                {isLoading ? "Se analizează jurisprudența..." : "Generează Memo Juridic"}
              </button>
           </div>
        </div>
        
        <div className="w-2/3 bg-white border border-slate-200 rounded-xl shadow-sm p-8 overflow-y-auto">
          {generatedContent ? (
             <div className="prose max-w-none text-slate-800 whitespace-pre-wrap">
               {generatedContent}
             </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-slate-400">
              <BookOpen size={48} className="mb-4 opacity-20" />
              <p>Rezultatul analizei va apărea aici.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen bg-slate-50 font-sans">
      {renderSidebar()}
      <main className="flex-1 overflow-hidden relative">
        {mode === 'dashboard' && renderFileContext()}
        {mode === 'drafter' && renderDrafter()}
        {mode === 'redflags' && renderRedFlags()}
        {mode === 'chat' && renderChat()}
        {mode === 'clarification' && renderClarification()}
        {mode === 'rag' && renderRAG()}
      </main>
    </div>
  );
};

const root = createRoot(document.getElementById("root")!);
root.render(<App />);