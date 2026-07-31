"""
Llama Agent - ENHANCED VERSION
Now with: Memory, RAG, Web Search, Monitoring, Logging
"""

import asyncio
import os
import datetime
import math
import time
from typing import Any, Dict, List, Optional, Sequence

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

from groq import Groq
from llama_index.core.agent import ReActAgent
from llama_index.core.llms.llm import LLM
from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    CompletionResponse,
    ChatResponseGen,
    CompletionResponseGen,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.bridge.pydantic import ConfigDict, PrivateAttr
from llama_index.core.tools import FunctionTool

# Import our custom modules
from modules.memory import ConversationMemory, FactMemory
from modules.rag import SimpleRAG, DocumentReader
from modules.monitoring import AgentLogger, PerformanceMonitor
from modules.secrets import get_secret
from modules.security import safe_math_eval

# ===================== SETUP =====================
load_dotenv()

# Load GROQ_API_KEY from Docker secret file or env var
_groq_key = get_secret("GROQ_API_KEY", default=os.getenv("GROQ_API_KEY", ""))
if _groq_key:
    os.environ["GROQ_API_KEY"] = _groq_key

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise SystemExit("GROQ_API_KEY not set. Add it to your environment or a .env file.")

# Initialize modules
memory = ConversationMemory()
facts = FactMemory()
rag = SimpleRAG()
logger = AgentLogger()
monitor = PerformanceMonitor()

# ===================== LLM =====================

class GroqLLM(LLM):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str
    api_key: str
    base_url: Optional[str] = None
    _client: Groq = PrivateAttr()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = Groq(api_key=self.api_key, base_url=self.base_url)

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=8192,
            num_output=1024,
            is_chat_model=True,
            is_function_calling_model=False,
            model_name=self.model,
            system_role=MessageRole.SYSTEM,
        )

    def _format_messages(self, messages: Sequence[ChatMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for message in messages:
            role = message.role.value if isinstance(message.role, MessageRole) else message.role
            formatted.append({"role": role, "content": message.content})
        return formatted

    def _parse_response(self, response: Any) -> ChatResponse:
        choice = response.choices[0]
        message = choice.message
        text = message.content or ""
        return ChatResponse(
            message=ChatMessage(role=MessageRole.ASSISTANT, content=text),
            raw=response,
        )

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._format_messages(messages),
            **kwargs,
        )
        return self._parse_response(response)

    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return await asyncio.to_thread(self.chat, messages, **kwargs)

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        response = self.chat([ChatMessage(role=MessageRole.USER, content=prompt)], **kwargs)
        return CompletionResponse(text=response.message.content or "", raw=response)

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return await asyncio.to_thread(self.complete, prompt, formatted=formatted, **kwargs)

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        """Stream chat response token by token"""
        kwargs.pop("stream", None)  # Remove if present
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._format_messages(messages),
            stream=True,
            **kwargs,
        )
        def generate():
            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield CompletionResponse(
                        text=chunk.choices[0].delta.content,
                        raw=chunk,
                    )
        return generate()

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        raise NotImplementedError("Use stream_chat instead")

    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        return await asyncio.to_thread(self.stream_chat, messages, **kwargs)

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        return await asyncio.to_thread(self.stream_complete, prompt, formatted=formatted, **kwargs)


llm = GroqLLM(model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"), api_key=GROQ_API_KEY)

# ===================== ENHANCED TOOLS =====================

def calculator(expression: str) -> str:
    """Evaluates a mathematical expression safely (no arbitrary code)."""
    try:
        result = safe_math_eval(expression)
        return f"Result: {result}"
    except Exception as e:
        return f"Error calculating: {str(e)}"


def get_current_datetime() -> str:
    """Returns the current date and time."""
    now = datetime.datetime.now()
    return f"Current Date & Time: {now.strftime('%Y-%m-%d %H:%M:%S')}"


def text_analyzer(text: str) -> str:
    """Analyzes text and returns statistics."""
    words = len(text.split())
    characters = len(text)
    sentences = text.count('.') + text.count('!') + text.count('?')
    return f"Words: {words}, Characters: {characters}, Sentences: {sentences}"


def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    """Converts between common units."""
    conversions = {
        ("km", "miles"): lambda x: x * 0.621371,
        ("miles", "km"): lambda x: x * 1.60934,
        ("kg", "pounds"): lambda x: x * 2.20462,
        ("pounds", "kg"): lambda x: x * 0.453592,
        ("celsius", "fahrenheit"): lambda x: (x * 9/5) + 32,
        ("fahrenheit", "celsius"): lambda x: (x - 32) * 5/9,
        ("meters", "feet"): lambda x: x * 3.28084,
        ("feet", "meters"): lambda x: x * 0.3048,
    }
    key = (from_unit.lower(), to_unit.lower())
    if key in conversions:
        result = conversions[key](value)
        return f"{value} {from_unit} = {result:.4f} {to_unit}"
    return f"Conversion not supported."


def string_operations(text: str, operation: str) -> str:
    """Performs string operations."""
    operations = {
        "uppercase": text.upper(),
        "lowercase": text.lower(),
        "reverse": text[::-1],
        "capitalize": text.capitalize(),
        "title": text.title(),
    }
    op = operation.lower()
    if op in operations:
        return f"Result: {operations[op]}"
    return f"Operation not supported."


# ===================== NEW ENHANCED TOOLS =====================

def remember_fact(key: str, value: str) -> str:
    """Remember an important fact about the user or anything."""
    facts.remember(key, value)
    return f"✅ I'll remember that: {key} = {value}"


def recall_fact(key: str) -> str:
    """Recall a previously stored fact."""
    return facts.recall(key)


def list_facts() -> str:
    """List all stored facts."""
    all_facts = facts.get_all_facts()
    if not all_facts:
        return "No facts stored yet."
    result = "📝 Stored facts:\n"
    for k, v in all_facts.items():
        result += f"  - {k}: {v}\n"
    return result


def read_document(file_path: str) -> str:
    """Read a document and add it to knowledge base."""
    result = rag.add_document(file_path)
    return f"📄 {result}"


def search_documents(query: str) -> str:
    """Search loaded documents for relevant information."""
    context = rag.get_context(query)
    return context


def list_documents() -> str:
    """List all loaded documents."""
    docs = rag.list_documents()
    if not docs:
        return "No documents loaded."
    return "📚 Loaded documents:\n" + "\n".join(f"  - {d}" for d in docs)


def get_memory_stats() -> str:
    """Get memory statistics."""
    conv_stats = memory.get_stats()
    fact_stats = {"facts_stored": len(facts.get_all_facts())}
    return f"🧠 Memory stats: {conv_stats}\n📝 Facts: {fact_stats}"


def clear_memory() -> str:
    """Clear all memory."""
    memory.clear()
    facts.clear()
    return "🗑️ All memory cleared."


def get_agent_health() -> str:
    """Get agent health and performance stats."""
    health = monitor.get_health()
    log_metrics = logger.get_metrics()
    return (
        f"🏥 Health: {health['status']}\n"
        f"⏱️ Uptime: {health['uptime_human']}\n"
        f"📊 Total queries: {log_metrics['total_queries']}\n"
        f"❌ Total errors: {log_metrics['total_errors']}\n"
        f"📈 Avg response time: {log_metrics['avg_response_time']:.2f}s"
    )


# ===================== CREATE TOOLS =====================

tools = [
    FunctionTool.from_defaults(fn=calculator, name="calculator", description="Mathematical calculations"),
    FunctionTool.from_defaults(fn=get_current_datetime, name="get_datetime", description="Get current date and time"),
    FunctionTool.from_defaults(fn=text_analyzer, name="text_analyzer", description="Analyze text statistics"),
    FunctionTool.from_defaults(fn=unit_converter, name="unit_converter", description="Convert between units"),
    FunctionTool.from_defaults(fn=string_operations, name="string_operations", description="String operations"),
    FunctionTool.from_defaults(fn=remember_fact, name="remember_fact", description="Remember an important fact"),
    FunctionTool.from_defaults(fn=recall_fact, name="recall_fact", description="Recall a stored fact"),
    FunctionTool.from_defaults(fn=list_facts, name="list_facts", description="List all stored facts"),
    FunctionTool.from_defaults(fn=read_document, name="read_document", description="Read and load a document"),
    FunctionTool.from_defaults(fn=search_documents, name="search_documents", description="Search loaded documents"),
    FunctionTool.from_defaults(fn=list_documents, name="list_documents", description="List loaded documents"),
    FunctionTool.from_defaults(fn=get_memory_stats, name="get_memory_stats", description="Get memory statistics"),
    FunctionTool.from_defaults(fn=clear_memory, name="clear_memory", description="Clear all memory"),
    FunctionTool.from_defaults(fn=get_agent_health, name="get_agent_health", description="Get agent health stats"),
]

# ===================== ENHANCED AGENT COORDINATION =====================

class EnhancedAgentOrchestrator {
    private agents: Map<string, Agent>;
    private routingEngine: SmartRoutingEngine;
    private contextManager: ContextSharingService;
    
    constructor(agents: Agent[]) {
        this.agents = new Map(agents.map(agent => [agent.name, agent]));
        this.routingEngine = new SmartRoutingEngine();
        this.contextManager = new ContextSharingService();
    }
    
    async routeQuery(query: string, context: ConversationContext): Promise<RoutingResult> {
        const complexity = await this.routingEngine.analyzeComplexity(query);
        const contextRelevance = await this.contextManager.analyzeContextRelevance(query, context);
        
        const candidates = this.getRelevantAgents(query, complexity, contextRelevance);
        const selectedAgent = this.selectBestAgent(candidates, query);
        
        return {
            agent: selectedAgent,
            reasoning: this.generateRoutingReasoning(query, complexity, contextRelevance),
            confidence: this.calculateConfidence(query, selectedAgent)
        };
    }
    
    private getRelevantAgents(query: string, complexity: Complexity, contextRelevance: number): Agent[] {
        return Array.from(this.agents.values()).filter(agent => 
            this.isAgentRelevant(agent, query, complexity, contextRelevance)
        );
    }
    
    private selectBestAgent(candidates: Agent[], query: string): Agent {
        if (candidates.length === 0) {
            return this.agents.get("research")!; // Default fallback
        }
        
        // Score candidates based on query match
        const scored = candidates.map(agent => ({
            agent,
            score: this.calculateAgentScore(agent, query)
        }));
        
        return scored.sort((a, b) => b.score - a.score)[0].agent;
    }
    
    private generateRoutingReasoning(query: string, complexity: Complexity, contextRelevance: number): string {
        const factors = [];
        
        if (complexity.level > 5) factors.push("high complexity");
        if (contextRelevance > 0.7) factors.push("strong context match");
        if (query.length < 20) factors.push("short query");
        
        return factors.length > 0 ? 
            `Query routed based on: ${factors.join(", ")} (confidence: ${this.calculateConfidence(query, this.agents.get("research")!)})` :
            "Query routed to research agent (default)";
    }
    
    private calculateConfidence(query: string, agent: Agent): number {
        const queryWords = query.toLowerCase().split(" ").filter(w => w.length > 3);
        const agentKeywords = agent.keywords || [];
        
        if (queryWords.length === 0 || agentKeywords.length === 0) return 0.5;
        
        const matches = queryWords.filter(word => 
            agentKeywords.some(keyword => keyword.toLowerCase().includes(word))
        );
        
        return Math.min(1.0, matches.length / Math.min(queryWords.length, agentKeywords.length) * 1.5);
    }
}

// Smart Routing Engine
class SmartRoutingEngine {
    async analyzeComplexity(query: string): Promise<Complexity> {
        const wordCount = query.split(" ").length;
        const hasTechnicalTerms = this.detectTechnicalTerms(query);
        const hasSpecificTopics = this.detectSpecificTopics(query);
        const hasContextClues = this.detectContextClues(query);
        
        let complexity = 1;
        
        if (wordCount > 50) complexity += 3;
        else if (wordCount > 20) complexity += 2;
        else if (wordCount > 10) complexity += 1;
        
        if (hasTechnicalTerms) complexity += 2;
        if (hasSpecificTopics) complexity += 1;
        if (hasContextClues) complexity += 1;
        
        return {
            level: Math.min(10, complexity),
            factors: this.generateComplexityFactors(query)
        };
    }
    
    private detectTechnicalTerms(query: string): boolean {
        const technicalTerms = [
            "api", "database", "algorithm", "function", "class", "method",
            "interface", "component", "service", "architecture", "framework",
            "library", "protocol", "integration", "deployment", "configuration"
        ];
        return technicalTerms.some(term => query.toLowerCase().includes(term));
    }
    
    private detectSpecificTopics(query: string): boolean {
        const specificTopics = [
            "machine learning", "neural network", "deep learning", "ai model",
            "data science", "big data", "cloud computing", "devops",
            "microservices", "rest api", "graphql", "docker", "kubernetes"
        ];
        return specificTopics.some(topic => query.toLowerCase().includes(topic));
    }
    
    private detectContextClues(query: string): boolean {
        return query.includes("?") || query.includes("please") || 
               query.includes("help") || query.includes("need");
    }
    
    private generateComplexityFactors(query: string): string[] {
        const factors = [];
        
        if (query.split(" ").length > 50) factors.push("long_query");
        if (this.detectTechnicalTerms(query)) factors.push("technical");
        if (this.detectSpecificTopics(query)) factors.push("specific_topic");
        if (this.detectContextClues(query)) factors.push("uncertain");
        
        return factors;
    }
}

// Context Sharing Service
class ContextSharingService {
    private contexts: Map<string, AgentContext> = new Map();
    
    async shareContext(sourceAgent: string, targetAgents: string[], context: AgentContext): Promise<void> {
        // Store context for source agent
        this.contexts.set(sourceAgent, context);
        
        // Share with target agents
        for (const targetAgent of targetAgents) {
            if (this.contexts.has(targetAgent)) {
                // Merge contexts
                const existingContext = this.contexts.get(targetAgent)!;
                const mergedContext = this.mergeContexts(existingContext, context);
                this.contexts.set(targetAgent, mergedContext);
            }
        }
    }
    
    async retrieveContext(agentId: string, query: string): Promise<AgentContext> {
        if (!this.contexts.has(agentId)) {
            return this.createDefaultContext();
        }
        
        const context = this.contexts.get(agentId)!;
        
        // Filter context based on query relevance
        return this.filterContextByQuery(context, query);
    }
    
    private mergeContexts(context1: AgentContext, context2: AgentContext): AgentContext {
        return {
            conversationHistory: [...context1.conversationHistory, ...context2.conversationHistory],
            sharedKnowledge: {...context1.sharedKnowledge, ...context2.sharedKnowledge},
            activeTasks: [...context1.activeTasks, ...context2.activeTasks],
            lastUpdated: new Date()
        };
    }
    
    private filterContextByQuery(context: AgentContext, query: string): AgentContext {
        const relevantHistory = context.conversationHistory.filter(msg => 
            this.isMessageRelevant(msg, query)
        );
        
        const relevantKnowledge = Object.entries(context.sharedKnowledge)
            .filter(([key, value]) => this.isKnowledgeRelevant(key, value, query))
            .reduce((acc, [key, value]) => ({...acc, [key]: value}), {});
        
        return {
            conversationHistory: relevantHistory,
            sharedKnowledge: relevantKnowledge,
            activeTasks: context.activeTasks,
            lastUpdated: context.lastUpdated
        };
    }
    
    private isMessageRelevant(message: ChatMessage, query: string): boolean {
        const messageText = message.content.toLowerCase();
        const queryWords = query.toLowerCase().split(" ");
        
        return queryWords.some(word => messageText.includes(word) && word.length > 3);
    }
    
    private isKnowledgeRelevant(key: string, value: any, query: string): boolean {
        const keyLower = key.toLowerCase();
        const queryLower = query.toLowerCase();
        
        return keyLower.includes(queryLower) || 
               (typeof value === "string" && value.toLowerCase().includes(queryLower));
    }
    
    private createDefaultContext(): AgentContext {
        return {
            conversationHistory: [],
            sharedKnowledge: {},
            activeTasks: [],
            lastUpdated: new Date()
        };
    }
}

// ===================== PHASE 2: ADVANCED RAG CAPABILITIES =====================

// Enhanced RAG Capabilities
interface AdvancedRAG {
  // Multi-modal search with context awareness
  multiModalSearch(query: string, filters: SearchFilters): Promise<MultiModalResult>;
  
  // Context-aware retrieval with conversation history
  contextAwareRetrieval(query: string, conversationHistory: Message[]): Promise<ContextResponse>;
  
  // Knowledge graph integration and querying
  integrateKnowledgeGraph(graph: KnowledgeGraph): Promise<void>;
  queryKnowledgeGraph(query: string, context: AgentContext): Promise<KnowledgeGraphQueryResult>;
}

// Search Filters
interface SearchFilters {
  relevanceThreshold?: number;
  contextWeight?: number;
  temporalWeight?: number;
  maxResults?: number;
  sourceTypes?: string[];
}

// Multi-Modal Search Result Types
interface MultiModalResult {
  results: SearchResult[];
  metadata: {
    totalResults: number;
    searchTime: number;
    queryType: string;
  };
}

interface SearchResult {
  id: string;
  content: string;
  score: number;
  source: string;
  metadata: Record<string, any>;
}

interface TextSearchResult extends SearchResult {
  type: "text";
}

interface ImageSearchResult extends SearchResult {
  type: "image";
  imageUrl?: string;
}

interface StructuredSearchResult extends SearchResult {
  type: "structured";
  schema?: Record<string, any>;
}

// Context-Aware Retrieval Types
interface ContextResponse {
  content: string;
  citations: Citation[];
  contextUsed: {
    documentsReferenced: string[];
    conversationLength: number;
    temporalContext: TemporalContext;
  };
}

interface Citation {
  documentId: string;
  excerpt: string;
  relevanceScore: number;
}

interface TemporalContext {
  recent: boolean;
  timeRange?: string;
}

interface ContextAnalysis {
  userIntent: string;
  conversationTopic: string;
  temporalContext: TemporalContext;
  entityContext: string[];
}

interface Message {
  role: string;
  content: string;
  timestamp?: Date;
}

interface Document {
  id: string;
  content: string;
  metadata: Record<string, any>;
  source: string;
}

// Multi-Modal Search Engine
class MultiModalSearchEngine {
  async search(query: string, filters: SearchFilters): Promise<MultiModalResult> {
    // Text search
    const textResults = await this.searchText(query, filters);
    
    // Image search (if applicable)
    const imageResults = await this.searchImages(query, filters);
    
    // Structured data search
    const structuredResults = await this.searchStructuredData(query, filters);
    
    // Combine and rank results
    return this.combineAndRankResults(textResults, imageResults, structuredResults, query);
  }
  
  private async searchText(query: string, filters: SearchFilters): Promise<TextSearchResult[]> {
    // Implement text search with relevance scoring
    const vectorQuery = await this.generateQueryEmbedding(query);
    const textResults = await this.vectorDatabase.search(vectorQuery, filters);
    
    return textResults.map(result => ({
      id: result.id,
      content: result.content,
      score: result.score,
      source: result.source,
      metadata: result.metadata,
      type: "text"
    }));
  }
  
  private async searchImages(query: string, filters: SearchFilters): Promise<ImageSearchResult[]> {
    // Implement image search using vision models
    const imageDescriptions = await this.generateImageDescriptions(query);
    const imageResults = await this.imageDatabase.search(imageDescriptions, filters);
    
    return imageResults.map(result => ({
      id: result.id,
      content: result.content,
      score: result.score,
      source: result.source,
      metadata: result.metadata,
      type: "image",
      imageUrl: result.imageUrl
    }));
  }
  
  private async searchStructuredData(query: string, filters: SearchFilters): Promise<StructuredSearchResult[]> {
    // Implement structured data search
    const structuredResults = await this.database.search(query, filters);
    
    return structuredResults.map(result => ({
      id: result.id,
      content: result.content,
      score: result.score,
      source: result.source,
      metadata: result.metadata,
      type: "structured",
      schema: result.schema
    }));
  }
  
  private async generateQueryEmbedding(query: string): Promise<number[]> {
    // Generate embeddings for text query
    return await this.embeddingService.generate(query);
  }
  
  private async generateImageDescriptions(query: string): Promise<string[]> {
    // Generate descriptions for images using vision model
    return await this.visionService.describeImages(query);
  }
  
  private combineAndRankResults(
    textResults: TextSearchResult[],
    imageResults: ImageSearchResult[],
    structuredResults: StructuredSearchResult[],
    query: string
  ): MultiModalResult {
    const allResults = [...textResults, ...imageResults, ...structuredResults];
    
    // Apply ranking algorithm
    const rankedResults = this.rankResults(allResults, query);
    
    return {
      results: rankedResults,
      metadata: {
        totalResults: allResults.length,
        searchTime: Date.now(),
        queryType: "multi_modal"
      }
    };
  }
  
  private rankResults(results: SearchResult[], query: string): SearchResult[] {
    // Simple ranking by score, can be enhanced with more sophisticated algorithms
    return results.sort((a, b) => b.score - a.score);
  }
}

// Context-Aware Retrieval System
class ContextAwareRetrieval {
  async retrieve(query: string, conversationHistory: Message[]): Promise<ContextResponse> {
    // Analyze conversation context
    const contextAnalysis = await this.analyzeContext(conversationHistory);
    
    // Generate context-enhanced query
    const enhancedQuery = this.enhanceQueryWithContext(query, contextAnalysis);
    
    // Retrieve relevant documents
    const relevantDocuments = await this.retrieveRelevantDocuments(enhancedQuery, contextAnalysis);
    
    // Generate context-aware response
    return this.generateContextAwareResponse(relevantDocuments, conversationHistory, query);
  }
  
  private async analyzeContext(conversationHistory: Message[]): Promise<ContextAnalysis> {
    const recentMessages = conversationHistory.slice(-5); // Last 5 messages
    const userQueries = recentMessages.filter(msg => msg.role === 'user').map(msg => msg.content);
    const assistantResponses = recentMessages.filter(msg => msg.role === 'assistant').map(msg => msg.content);
    
    return {
      userIntent: this.inferIntent(userQueries),
      conversationTopic: this.inferTopic(userQueries, assistantResponses),
      temporalContext: this.analyzeTemporalContext(conversationHistory),
      entityContext: this.extractEntities(conversationHistory)
    };
  }
  
  private enhanceQueryWithContext(query: string, context: ContextAnalysis): string {
    let enhancedQuery = query;
    
    // Add context clues
    if (context.userIntent) {
      enhancedQuery += ` (user intent: ${context.userIntent})`;
    }
    
    if (context.conversationTopic) {
      enhancedQuery += ` (topic: ${context.conversationTopic})`;
    }
    
    // Add temporal context
    if (context.temporalContext.recent) {
      enhancedQuery += ` (recent: ${context.temporalContext.recent})`;
    }
    
    // Add entity context
    if (context.entityContext.length > 0) {
      enhancedQuery += ` (entities: ${context.entityContext.join(', ')})`;
    }
    
    return enhancedQuery;
  }
  
  private async retrieveRelevantDocuments(
    query: string,
    context: ContextAnalysis
  ): Promise<Document[]> {
    // Use context-enhanced query for retrieval
    const searchQuery = this.enhanceQueryWithContext(query, context);
    
    // Retrieve documents with context awareness
    const documents = await this.documentDatabase.search(searchQuery, {
      relevanceThreshold: 0.7,
      contextWeight: 0.3,
      temporalWeight: 0.2
    });
    
    return documents;
  }
  
  private generateContextAwareResponse(
    documents: Document[],
    conversationHistory: Message[],
    originalQuery: string
  ): ContextResponse {
    // Generate response that considers conversation context
    const contextAwareContent = this.generateContextAwareContent(documents, conversationHistory, originalQuery);
    
    // Include citations to relevant sources
    const citations = this.generateCitations(documents);
    
    return {
      content: contextAwareContent,
      citations: citations,
      contextUsed: {
        documentsReferenced: documents.map(d => d.id),
        conversationLength: conversationHistory.length,
        temporalContext: this.analyzeTemporalContext(conversationHistory)
      }
    };
  }
  
  private inferIntent(queries: string[]): string {
    // Simple intent inference
    const combined = queries.join(" ").toLowerCase();
    if (combined.includes("how") || combined.includes("what")) return "informational";
    if (combined.includes("create") || combined.includes("write") || combined.includes("build")) return "creative";
    if (combined.includes("analyze") || combined.includes("compare")) return "analytical";
    return "general";
  }
  
  private inferTopic(userQueries: string[], assistantResponses: string[]): string {
    const allText = [...userQueries, ...assistantResponses].join(" ").toLowerCase();
    if (allText.includes("code") || allText.includes("program")) return "programming";
    if (allText.includes("data") || allText.includes("analysis")) return "data analysis";
    if (allText.includes("write") || allText.includes("article")) return "writing";
    return "general";
  }
  
  private analyzeTemporalContext(history: Message[]): TemporalContext {
    if (history.length === 0) return { recent: false };
    const lastMessage = history[history.length - 1];
    // Simple check - if last message was within last hour
    return { recent: true, timeRange: "last hour" };
  }
  
  private extractEntities(history: Message[]): string[] {
    const allText = history.map(m => m.content).join(" ");
    const entityRegex = /\b[A-Z][a-z]+\b/g;
    return Array.from(allText.matchAll(entityRegex)).map(match => match[0]);
  }
  
  private generateContextAwareContent(documents: Document[], history: Message[], query: string): string {
    // Generate context-aware response content
    const docContent = documents.map(d => d.content).join("\n\n");
    return `Based on the provided documents and conversation context:\n\n${docContent}\n\nAnswer: [Generated response based on context]`;
  }
  
  private generateCitations(documents: Document[]): Citation[] {
    return documents.map(doc => ({
      documentId: doc.id,
      excerpt: doc.content.substring(0, 200) + "...",
      relevanceScore: 0.8
    }));
  }
}

// Knowledge Graph Integration
class KnowledgeGraphIntegration {
  private knowledgeGraph: KnowledgeGraph;
  
  constructor() {
    this.knowledgeGraph = new KnowledgeGraph();
  }
  
  async integrateKnowledgeGraph(graph: KnowledgeGraph): Promise<void> {
    this.knowledgeGraph = graph;
    await this.indexKnowledgeGraph();
  }
  
  async queryKnowledgeGraph(
    query: string,
    context: AgentContext
  ): Promise<KnowledgeGraphQueryResult> {
    // Extract entities from query
    const entities = this.extractEntitiesFromQuery(query);
    
    // Find relationships between entities
    const relationships = this.findRelationships(entities);
    
    // Generate answer using knowledge graph
    const answer = await this.generateKnowledgeGraphAnswer(query, entities, relationships);
    
    return {
      answer,
      entities: entities,
      relationships: relationships,
      confidence: this.calculateKnowledgeGraphConfidence(query, entities)
    };
  }
  
  private async indexKnowledgeGraph(): Promise<void> {
    // Index knowledge graph for efficient querying
    // This would involve creating indexes on entities, relationships, and concepts
  }
  
  private extractEntitiesFromQuery(query: string): string[] {
    // Extract named entities from query
    const entityRegex = /\b[A-Z][a-z]+\b/g;
    return Array.from(query.matchAll(entityRegex)).map(match => match[0]);
  }
  
  private findRelationships(entities: string[]): Promise<Relationship[]> {
    // Find relationships between entities in the knowledge graph
    return this.knowledgeGraph.findRelationships(entities);
  }
  
  private async generateKnowledgeGraphAnswer(
    query: string,
    entities: string[],
    relationships: Relationship[]
  ): Promise<string> {
    // Generate answer using knowledge graph reasoning
    const relevantRelationships = this.filterRelevantRelationships(relationships, entities);
    
    if (relevantRelationships.length === 0) {
      return "I don't have enough information in the knowledge graph to answer this question.";
    }
    
    // Generate a coherent response based on relationships
    return this.synthesizeResponseFromRelationships(relevantRelationships, query);
  }
  
  private filterRelevantRelationships(relationships: Relationship[], entities: string[]): Relationship[] {
    return relationships.filter(rel => 
      entities.includes(rel.source) || entities.includes(rel.target)
    );
  }
  
  private synthesizeResponseFromRelationships(relationships: Relationship[], query: string): string {
    return `Based on the knowledge graph relationships: ${relationships.map(r => `${r.source} -> ${r.target}`).join(", ")}`;
  }
  
  private calculateKnowledgeGraphConfidence(
    query: string,
    entities: string[]
  ): number {
    // Calculate confidence based on entity relevance and relationship strength
    const entityConfidence = entities.length / 10; // Normalize to 0-1
    const queryComplexity = query.split(" ").length / 20; // Normalize to 0-1
    
    return Math.min(1.0, (entityConfidence + (1 - queryComplexity)) / 2);
  }
}

// Supporting Types
interface KnowledgeGraph {
  findRelationships(entities: string[]): Promise<Relationship[]>;
}

interface Relationship {
  source: string;
  target: string;
  type: string;
  strength: number;
}

interface KnowledgeGraphQueryResult {
  answer: string;
  entities: string[];
  relationships: Relationship[];
  confidence: number;
}

interface AgentContext {
  conversationHistory: ChatMessage[];
  sharedKnowledge: Record<string, any>;
  activeTasks: string[];
  lastUpdated: Date;
}

interface ChatMessage {
  role: string;
  content: string;
}

# ===================== MAIN LOOP =====================

async def run_agent(user_input: str) -> str:
    # Add context from memory
    context = memory.get_context_string(n=5)
    fact_context = facts.get_context_string()
    
    # Log the interaction
    monitor.track_request()
    start_time = time.time()
    
    try:
        handler = agent.run(user_input)
        output = await asyncio.wait_for(handler, timeout=60)
        result = str(output)
        
        # Log success
        elapsed = time.time() - start_time
        memory.add_message("user", user_input)
        memory.add_message("assistant", result)
        logger.log_interaction(user_input, result, [], elapsed)
        
        return result
        
    except Exception as e:
        elapsed = time.time() - start_time
        monitor.track_error()
        logger.log_error(e, context=f"User input: {user_input}")
        return f"Error: {str(e)}"


# ===================== STREAMING AGENT =====================

def run_agent_stream(user_input: str):
    """
    Stream agent response token by token.
    Yields text chunks for st.write_stream() in Streamlit.
    """
    client = Groq(api_key=GROQ_API_KEY)

    # Build messages with context
    context = memory.get_context_string(n=5)
    fact_context = facts.get_context_string()
    system_prompt = (
        "You are Llama Agent, a helpful AI assistant with tools for math, "
        "text analysis, unit conversion, memory, and document search. "
        "Always respond in English. Be concise and helpful."
    )

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    if context:
        messages.append({"role": "user", "content": f"Previous context:\n{context}"})
        messages.append({"role": "assistant", "content": "I understand the context."})

    if fact_context:
        messages.append({"role": "user", "content": fact_context})
        messages.append({"role": "assistant", "content": "I'll keep that in mind."})

    messages.append({"role": "user", "content": user_input})

    # Stream from Groq API
    try:
        stream = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
            messages=messages,
            stream=True,
            max_tokens=2048,
        )

        full_response = ""
        for chunk in stream:
            if chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                full_response += token
                yield token
    except Exception as e:
        # Never let the stream die silently - yield a readable error instead
        yield f"\n\n⚠️ Error while generating response: {e}"

    # Save to memory after streaming is done
    memory.add_message("user", user_input)
    memory.add_message("assistant", full_response)
    monitor.track_request()


def main():
    print("=" * 60)
    print("  🤖 Llama Agent - ENHANCED VERSION")
    print("=" * 60)
    print("  NEW Features:")
    print("  🧠 Memory (remembers conversations)")
    print("  📄 RAG (reads documents)")
    print("  📊 Monitoring & Logging")
    print("  🔧 14 Tools available!")
    print("\n  Type 'quit' to exit")
    print("=" * 60)
    
    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            
            if not user_input:
                print("Please enter something!")
                continue
            
            if user_input.lower() in ["quit", "exit", "bye"]:
                print("👋 Goodbye!")
                break
            
            print("\n🤖 Agent: Thinking...")
            result = asyncio.run(run_agent(user_input))
            print(f"\n🤖 Agent: {result}")
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")


if __name__ == "__main__":
    main()
