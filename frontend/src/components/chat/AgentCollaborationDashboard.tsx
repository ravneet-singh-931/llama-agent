"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Loader2, Bot, Zap, Brain, Search, FileText, Users, Activity } from "lucide-react";
import { cn } from "@/lib/utils";

interface AgentStatus {
  name: string;
  status: "active" | "idle" | "busy";
  currentTask?: string;
  lastActive: Date;
  tasksCompleted: number;
  avgResponseTime: number;
}

interface CollaborationMetrics {
  totalAgents: number;
  activeAgents: number;
  tasksInProgress: number;
  tasksCompleted: number;
  avgCollaborationTime: number;
}

export function AgentCollaborationDashboard() {
  const [agents, setAgents] = useState<AgentStatus[]>([
    { name: "Research Agent", status: "active", currentTask: "Analyzing market trends", lastActive: new Date(), tasksCompleted: 42, avgResponseTime: 2.3 },
    { name: "Coder Agent", status: "busy", currentTask: "Writing React components", lastActive: new Date(), tasksCompleted: 38, avgResponseTime: 1.8 },
    { name: "Writer Agent", status: "idle", currentTask: "Waiting for task", lastActive: new Date(Date.now() - 300000), tasksCompleted: 25, avgResponseTime: 3.1 },
    { name: "Planner Agent", status: "active", currentTask: "Creating project roadmap", lastActive: new Date(), tasksCompleted: 18, avgResponseTime: 4.2 },
    { name: "Analyzer Agent", status: "idle", currentTask: "Waiting for data", lastActive: new Date(Date.now() - 600000), tasksCompleted: 15, avgResponseTime: 2.7 },
  ]);

  const [metrics, setMetrics] = useState<CollaborationMetrics>({
    totalAgents: 5,
    activeAgents: 2,
    tasksInProgress: 3,
    tasksCompleted: 138,
    avgCollaborationTime: 2.8,
  });

  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    // Simulate real-time updates
    const interval = setInterval(() => {
      setAgents(prev => prev.map(agent => ({
        ...agent,
        lastActive: Math.random() > 0.7 ? new Date() : agent.lastActive,
        status: Math.random() > 0.8 ? (agent.status === "idle" ? "active" : "idle") : agent.status,
        tasksCompleted: agent.tasksCompleted + (Math.random() > 0.95 ? 1 : 0),
      })));
      
      setMetrics(prev => ({
        ...prev,
        tasksCompleted: prev.tasksCompleted + (Math.random() > 0.9 ? 1 : 0),
        activeAgents: Math.floor(Math.random() * 3) + 2,
        tasksInProgress: Math.floor(Math.random() * 4) + 1,
      }));
    }, 5000);

    return () => clearInterval(interval);
  }, []);

  const getStatusColor = (status: string) => {
    switch (status) {
      case "active": return "bg-emerald-500";
      case "busy": return "bg-amber-500";
      case "idle": return "bg-gray-400";
      default: return "bg-gray-400";
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case "active": return "Active";
      case "busy": return "Busy";
      case "idle": return "Idle";
      default: return "Unknown";
    }
  };

  return (
    <div className="space-y-6">
      {/* Metrics Overview */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Agents</CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{metrics.totalAgents}</div>
            <p className="text-xs text-muted-foreground">Registered agents</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Agents</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-600">{metrics.activeAgents}</div>
            <p className="text-xs text-muted-foreground">Currently active</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Tasks in Progress</CardTitle>
            <Loader2 className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-amber-600">{metrics.tasksInProgress}</div>
            <p className="text-xs text-muted-foreground">Currently processing</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Tasks Completed</CardTitle>
            <Zap className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{metrics.tasksCompleted}</div>
            <p className="text-xs text-muted-foreground">All time total</p>
          </CardContent>
        </Card>
      </div>

      {/* Agent Status Grid */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5" />
            Agent Status & Collaboration
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {agents.map((agent) => (
              <div key={agent.name} className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 p-4 bg-muted/50 rounded-lg border">
                <div className="flex items-center gap-4">
                  <div className={cn("w-3 h-3 rounded-full", getStatusColor(agent.status))} />
                  <div>
                    <div className="font-medium">{agent.name}</div>
                    <div className="text-sm text-muted-foreground">
                      {agent.currentTask ? `Current: ${agent.currentTask}` : "Waiting for task"}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-4 text-sm text-muted-foreground">
                  <span className={cn("flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium", 
                    agent.status === "active" && "bg-emerald-100 text-emerald-700",
                    agent.status === "busy" && "bg-amber-100 text-amber-700",
                    agent.status === "idle" && "bg-gray-100 text-gray-700"
                  )}>
                    {getStatusText(agent.status)}
                  </span>
                  <span>Tasks: {agent.tasksCompleted}</span>
                  <span>Avg: {agent.avgResponseTime}s</span>
                  <span>Last: {Math.floor((Date.now() - agent.lastActive.getTime()) / 60000)}m ago</span>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Collaboration Controls */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-5 w-5" />
            Team Collaboration Controls
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4">
            <Button 
              onClick={() => setIsLoading(true)}
              disabled={isLoading}
              className="gap-2"
            >
              <Loader2 className={cn("h-4 w-4", isLoading && "animate-spin")} />
              {isLoading ? "Coordinating..." : "Trigger Team Coordination"}
            </Button>
            <Button variant="outline" className="gap-2">
              <Search className="h-4 w-4" />
              Analyze Team Performance
            </Button>
            <Button variant="outline" className="gap-2">
              <Brain className="h-4 w-4" />
              Optimize Agent Allocation
            </Button>
            <Button variant="outline" className="gap-2">
              <FileText className="h-4 w-4" />
              Generate Collaboration Report
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}