"""
Advanced Bidirectional Fallback System for FenixAI Trading Bot
Includes predictive health checks, automatic recovery, and intelligent load balancing
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger(__name__)


class ProviderHealth(Enum):
    """Provider health states"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    MAINTENANCE = "maintenance"


class FallbackStrategy(Enum):
    """Fallback strategies"""
    IMMEDIATE = "immediate"  # Immediate fallback on error
    RETRY_FIRST = "retry_first"  # Retry before fallback
    CIRCUIT_BREAKER = "circuit_breaker"  # Circuit breaker pattern
    ADAPTIVE = "adaptive"  # Adaptive based on history


@dataclass
class HealthMetrics:
    """Health metrics for a provider"""
    provider: str
    health_status: ProviderHealth = ProviderHealth.UNKNOWN
    response_times: deque = field(default_factory=lambda: deque(maxlen=100))
    error_rate: float = 0.0
    success_rate: float = 100.0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    last_success: Optional[float] = None
    last_failure: Optional[float] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    avg_response_time: float = 0.0
    p95_response_time: float = 0.0
    cost_per_request: float = 0.0
    total_cost: float = 0.0
    
    def update_success(self, response_time: float, cost: float = 0.0):
        """Update success metrics"""
        current_time = time.time()
        self.total_requests += 1
        self.successful_requests += 1
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.last_success = current_time
        self.total_cost += cost
        
        self.response_times.append(response_time)
        self.success_rate = (self.successful_requests / self.total_requests) * 100
        self.error_rate = 100 - self.success_rate
        
        if self.response_times:
            self.avg_response_time = sum(self.response_times) / len(self.response_times)
            sorted_times = sorted(self.response_times)
            self.p95_response_time = sorted_times[int(len(sorted_times) * 0.95)]
        
        # Update health status
        self._update_health_status()
    
    def update_failure(self, error: str):
        """Update failure metrics"""
        current_time = time.time()
        self.total_requests += 1
        self.failed_requests += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure = current_time
        
        self.success_rate = (self.successful_requests / self.total_requests) * 100
        self.error_rate = 100 - self.success_rate
        
        # Update health status
        self._update_health_status()
    
    def _update_health_status(self):
        """Update health status based on metrics"""
        current_time = time.time()
        
        # If there are many consecutive failures
        if self.consecutive_failures >= 5:
            self.health_status = ProviderHealth.UNHEALTHY
            return
        
        # If the error rate is very high in recent requests
        if self.error_rate > 50 and self.total_requests >= 10:
            self.health_status = ProviderHealth.UNHEALTHY
            return
        
        # If there has been no recent success
        if (self.last_success and 
            current_time - self.last_success > 300):  # 5 minutes
            self.health_status = ProviderHealth.DEGRADED
            return
        
        # If the response time is very high
        if self.avg_response_time > 10000:  # 10 seconds
            self.health_status = ProviderHealth.DEGRADED
            return
        
        # If everything is fine
        if self.error_rate < 10 and self.avg_response_time < 5000:
            self.health_status = ProviderHealth.HEALTHY
        elif self.error_rate < 25:
            self.health_status = ProviderHealth.DEGRADED
        else:
            self.health_status = ProviderHealth.UNHEALTHY


@dataclass
class FallbackConfig:
    """Fallback system configuration"""
    max_retries: int = 3
    retry_delay: float = 1.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0
    health_check_interval: float = 30.0
    response_timeout: float = 10.0
    fallback_strategy: FallbackStrategy = FallbackStrategy.ADAPTIVE
    enable_predictive_health: bool = True
    enable_load_balancing: bool = True
    cost_optimization: bool = True


class BidirectionalFallbackSystem:
    """Bidirectional Fallback System with Predictive Health Checks"""
    
    def __init__(self, config: Optional[FallbackConfig] = None):
        self.config = config or FallbackConfig()
        self.providers_health: Dict[str, HealthMetrics] = {}
        self.circuit_breakers: Dict[str, Dict[str, Any]] = {}
        self.load_balancer_weights: Dict[str, float] = {}
        self.fallback_chains: Dict[str, List[str]] = {}
        self.health_check_task: Optional[asyncio.Task] = None
        self.is_monitoring = False
        
        # System statistics
        self.system_stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'fallback_requests': 0,
            'circuit_breaker_trips': 0,
            'provider_switches': 0,
            'cost_optimizations': 0,
            'predictive_interventions': 0
        }
        
        logger.info("🔄 BidirectionalFallbackSystem initialized with strategy: %s", 
                   self.config.fallback_strategy.value)
    
    def register_provider(self, provider_name: str, priority: int = 1):
        """Register a provider in the system"""
        if provider_name not in self.providers_health:
            self.providers_health[provider_name] = HealthMetrics(provider=provider_name)
            self.circuit_breakers[provider_name] = {
                'state': 'closed',  # closed, open, half-open
                'failure_count': 0,
                'last_failure_time': 0,
                'next_attempt_time': 0
            }
            self.load_balancer_weights[provider_name] = priority
            
            logger.info("✅ Provider '%s' registered with priority %d", provider_name, priority)
    
    def set_fallback_chain(self, agent_type: str, provider_chain: List[str]):
        """Set fallback chain for an agent type"""
        self.fallback_chains[agent_type] = provider_chain
        logger.info("🔗 Fallback chain for '%s': %s", agent_type, " → ".join(provider_chain))
    
    async def start_monitoring(self):
        """Start predictive health monitoring"""
        if not self.is_monitoring:
            self.is_monitoring = True
            self.health_check_task = asyncio.create_task(self._health_monitor_loop())
            logger.info("🔍 Predictive health monitoring started")
    
    async def stop_monitoring(self):
        """Stop health monitoring"""
        if self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass
            self.is_monitoring = False
            logger.info("⏹️ Health monitoring stopped")
    
    async def _health_monitor_loop(self):
        """Main health monitoring loop"""
        while self.is_monitoring:
            try:
                await self._perform_health_checks()
                await self._update_load_balancer_weights()
                await self._check_predictive_interventions()
                await asyncio.sleep(self.config.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in health monitor: %s", e)
                await asyncio.sleep(5)
    
    async def _perform_health_checks(self):
        """Perform health checks for all providers"""
        for provider_name, metrics in self.providers_health.items():
            try:
                # Check circuit breaker
                cb = self.circuit_breakers[provider_name]
                current_time = time.time()
                
                if cb['state'] == 'open':
                    if current_time >= cb['next_attempt_time']:
                        cb['state'] = 'half-open'
                        logger.info("🔄 Circuit breaker for '%s' moved to half-open", provider_name)
                
                # Predictive health check based on trends
                if self.config.enable_predictive_health:
                    await self._predictive_health_check(provider_name, metrics)
                    
            except Exception as e:
                logger.error("Health check failed for '%s': %s", provider_name, e)
    
    async def _predictive_health_check(self, provider_name: str, metrics: HealthMetrics):
        """Predictive health check based on trends"""
        current_time = time.time()
        
        # Prediction based on error trend
        if len(metrics.response_times) >= 10:
            recent_times = list(metrics.response_times)[-10:]
            avg_recent = sum(recent_times) / len(recent_times)
            
            # If response time is increasing dramatically
            if avg_recent > metrics.avg_response_time * 2:
                if metrics.health_status == ProviderHealth.HEALTHY:
                    metrics.health_status = ProviderHealth.DEGRADED
                    self.system_stats['predictive_interventions'] += 1
                    logger.warning("🔮 Predictive intervention: %s degraded due to response time trend", 
                                 provider_name)
        
        # Prediction based on time since last success
        if metrics.last_success and current_time - metrics.last_success > 120:  # 2 minutes
            if metrics.health_status == ProviderHealth.HEALTHY:
                metrics.health_status = ProviderHealth.DEGRADED
                self.system_stats['predictive_interventions'] += 1
                logger.warning("🔮 Predictive intervention: %s degraded due to no recent success", 
                             provider_name)
    
    async def _update_load_balancer_weights(self):
        """Update load balancer weights based on health"""
        if not self.config.enable_load_balancing:
            return
        
        for provider_name, metrics in self.providers_health.items():
            base_weight = 1.0
            
            # Adjust weight based on health
            if metrics.health_status == ProviderHealth.HEALTHY:
                health_multiplier = 1.0
            elif metrics.health_status == ProviderHealth.DEGRADED:
                health_multiplier = 0.5
            elif metrics.health_status == ProviderHealth.UNHEALTHY:
                health_multiplier = 0.1
            else:
                health_multiplier = 0.0
            
            # Adjust weight based on performance
            if metrics.avg_response_time > 0:
                # Lower response time = higher weight
                time_multiplier = min(1.0, 1000 / max(metrics.avg_response_time, 100))
            else:
                time_multiplier = 1.0
            
            # Adjust weight based on cost if enabled
            cost_multiplier = 1.0
            if self.config.cost_optimization and metrics.cost_per_request > 0:
                # Lower cost = higher weight
                avg_cost = sum(m.cost_per_request for m in self.providers_health.values()) / len(self.providers_health)
                if avg_cost > 0:
                    cost_multiplier = min(2.0, avg_cost / max(metrics.cost_per_request, 0.001))
            
            # Calculate final weight
            final_weight = base_weight * health_multiplier * time_multiplier * cost_multiplier
            self.load_balancer_weights[provider_name] = max(0.01, final_weight)  # Minimum weight
    
    async def _check_predictive_interventions(self):
        """Check if predictive interventions are needed"""
        # Check if all providers are degraded
        healthy_providers = [
            name for name, metrics in self.providers_health.items()
            if metrics.health_status == ProviderHealth.HEALTHY
        ]
        
        if len(healthy_providers) == 0:
            logger.warning("🚨 All providers are unhealthy or degraded!")
            # Notifications could be implemented here
        
        # Check cost patterns
        if self.config.cost_optimization:
            await self._optimize_cost_patterns()
    
    async def _optimize_cost_patterns(self):
        """Optimize cost patterns"""
        total_cost = sum(m.total_cost for m in self.providers_health.values())
        if total_cost > 0:
            # Identify the most expensive provider
            most_expensive = max(
                self.providers_health.items(),
                key=lambda x: x[1].cost_per_request if x[1].total_requests > 0 else 0
            )
            
            if most_expensive[1].cost_per_request > 0:
                # Reduce weight of the most expensive provider if healthy alternatives exist
                healthy_alternatives = [
                    name for name, metrics in self.providers_health.items()
                    if (metrics.health_status == ProviderHealth.HEALTHY and 
                        name != most_expensive[0])
                ]
                
                if healthy_alternatives:
                    current_weight = self.load_balancer_weights.get(most_expensive[0], 1.0)
                    self.load_balancer_weights[most_expensive[0]] = current_weight * 0.8
                    self.system_stats['cost_optimizations'] += 1
    
    def _get_circuit_breaker_state(self, provider_name: str) -> str:
        """Get circuit breaker state"""
        return self.circuit_breakers.get(provider_name, {}).get('state', 'closed')
    
    def _should_trip_circuit_breaker(self, provider_name: str) -> bool:
        """Check if the circuit breaker should be tripped"""
        cb = self.circuit_breakers.get(provider_name, {})
        metrics = self.providers_health.get(provider_name)
        
        if not metrics:
            return False
        
        return (cb.get('failure_count', 0) >= self.config.circuit_breaker_threshold or
                metrics.consecutive_failures >= self.config.circuit_breaker_threshold)
    
    def _trip_circuit_breaker(self, provider_name: str):
        """Trip circuit breaker"""
        current_time = time.time()
        cb = self.circuit_breakers[provider_name]
        cb['state'] = 'open'
        cb['last_failure_time'] = current_time
        cb['next_attempt_time'] = current_time + self.config.circuit_breaker_timeout
        
        self.system_stats['circuit_breaker_trips'] += 1
        logger.warning("⚡ Circuit breaker TRIPPED for '%s' - cooling down for %.1fs", 
                      provider_name, self.config.circuit_breaker_timeout)
    
    def get_optimal_provider(self, agent_type: str, exclude: Optional[List[str]] = None) -> Optional[str]:
        """Get the optimal provider for an agent"""
        exclude = exclude or []
        
        # Get fallback chain for the agent
        available_providers = self.fallback_chains.get(agent_type, list(self.providers_health.keys()))
        
        # Filter excluded providers and those with an open circuit breaker
        candidates = []
        for provider_name in available_providers:
            if provider_name in exclude:
                continue
            
            cb_state = self._get_circuit_breaker_state(provider_name)
            if cb_state == 'open':
                continue
            
            candidates.append(provider_name)
        
        if not candidates:
            return None
        
        # If no load balancing, use the first one available
        if not self.config.enable_load_balancing:
            return candidates[0]
        
        # Selection based on weights
        if self.config.fallback_strategy == FallbackStrategy.ADAPTIVE:
            # Select based on health, performance, and cost
            best_provider = None
            best_score = 0
            
            for provider_name in candidates:
                metrics = self.providers_health.get(provider_name)
                if not metrics:
                    continue
                
                weight = self.load_balancer_weights.get(provider_name, 1.0)
                health_score = {
                    ProviderHealth.HEALTHY: 1.0,
                    ProviderHealth.DEGRADED: 0.5,
                    ProviderHealth.UNHEALTHY: 0.1,
                    ProviderHealth.UNKNOWN: 0.3
                }.get(metrics.health_status, 0.1)
                
                total_score = weight * health_score
                
                if total_score > best_score:
                    best_score = total_score
                    best_provider = provider_name
            
            return best_provider or candidates[0]
        
        # For other strategies, use the first one available
        return candidates[0]
    
    async def execute_with_fallback(
        self,
        agent_type: str,
        execute_func,
        *args,
        **kwargs
    ) -> Tuple[Any, str]:
        """
        Execute function with fallback system
        Returns (result, provider_used)
        """
        self.system_stats['total_requests'] += 1
        
        attempted_providers = []
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            # Get optimal provider
            provider_name = self.get_optimal_provider(agent_type, exclude=attempted_providers)
            
            if not provider_name:
                break
            
            attempted_providers.append(provider_name)
            
            # Check circuit breaker
            cb_state = self._get_circuit_breaker_state(provider_name)
            if cb_state == 'open':
                continue
            
            start_time = time.time()
            
            try:
                # Execute function with timeout
                result = await asyncio.wait_for(
                    execute_func(provider_name, *args, **kwargs),
                    timeout=self.config.response_timeout
                )
                
                # Register success
                response_time = (time.time() - start_time) * 1000  # ms
                cost = kwargs.get('estimated_cost', 0.0)
                
                metrics = self.providers_health[provider_name]
                metrics.update_success(response_time, cost)
                
                # Reset circuit breaker if it was in half-open state
                cb = self.circuit_breakers[provider_name]
                if cb['state'] == 'half-open':
                    cb['state'] = 'closed'
                    cb['failure_count'] = 0
                    logger.info("✅ Circuit breaker for '%s' closed after successful request", provider_name)
                
                self.system_stats['successful_requests'] += 1
                
                if attempt > 0:
                    self.system_stats['fallback_requests'] += 1
                
                if provider_name != self.get_optimal_provider(agent_type):
                    self.system_stats['provider_switches'] += 1
                
                return result, provider_name
                
            except asyncio.TimeoutError:
                error_msg = f"Timeout after {self.config.response_timeout}s"
                logger.warning("⏰ %s timeout for '%s'", provider_name, agent_type)
                last_error = error_msg
                
            except Exception as e:
                error_msg = str(e)
                logger.warning("❌ %s failed for '%s': %s", provider_name, agent_type, error_msg)
                last_error = e
            
            # Register failure
            metrics = self.providers_health[provider_name]
            metrics.update_failure(str(last_error))
            
            # Update circuit breaker
            cb = self.circuit_breakers[provider_name]
            cb['failure_count'] += 1
            
            # Trip circuit breaker if necessary
            if self._should_trip_circuit_breaker(provider_name):
                self._trip_circuit_breaker(provider_name)
            
            # Delay before next attempt
            if attempt < self.config.max_retries:
                delay = self.config.retry_delay * (2 ** attempt)  # Exponential backoff
                await asyncio.sleep(delay)
        
        # All attempts failed
        error_msg = f"All fallback attempts failed for '{agent_type}'. Last error: {last_error}"
        logger.error("💥 %s", error_msg)
        raise RuntimeError(error_msg)
    
    def get_health_summary(self) -> Dict[str, Any]:
        """Get system health summary"""
        provider_summaries = {}
        
        for provider_name, metrics in self.providers_health.items():
            cb_state = self._get_circuit_breaker_state(provider_name)
            weight = self.load_balancer_weights.get(provider_name, 1.0)
            
            provider_summaries[provider_name] = {
                'health_status': metrics.health_status.value,
                'success_rate': round(metrics.success_rate, 2),
                'error_rate': round(metrics.error_rate, 2),
                'avg_response_time': round(metrics.avg_response_time, 2),
                'p95_response_time': round(metrics.p95_response_time, 2),
                'total_requests': metrics.total_requests,
                'consecutive_failures': metrics.consecutive_failures,
                'consecutive_successes': metrics.consecutive_successes,
                'circuit_breaker_state': cb_state,
                'load_balancer_weight': round(weight, 3),
                'total_cost': round(metrics.total_cost, 4),
                'cost_per_request': round(metrics.cost_per_request, 4),
                'last_success_ago': time.time() - metrics.last_success if metrics.last_success else None,
                'last_failure_ago': time.time() - metrics.last_failure if metrics.last_failure else None
            }
        
        return {
            'system_stats': self.system_stats,
            'providers': provider_summaries,
            'fallback_chains': self.fallback_chains,
            'config': {
                'strategy': self.config.fallback_strategy.value,
                'max_retries': self.config.max_retries,
                'circuit_breaker_threshold': self.config.circuit_breaker_threshold,
                'health_check_interval': self.config.health_check_interval,
                'predictive_health_enabled': self.config.enable_predictive_health,
                'load_balancing_enabled': self.config.enable_load_balancing,
                'cost_optimization_enabled': self.config.cost_optimization
            }
        }
    
    def get_recommendations(self) -> List[str]:
        """Get optimization recommendations"""
        recommendations = []
        
        # General health analysis
        unhealthy_providers = [
            name for name, metrics in self.providers_health.items()
            if metrics.health_status == ProviderHealth.UNHEALTHY
        ]
        
        if unhealthy_providers:
            recommendations.append(
                f"🚨 Unhealthy providers detected: {', '.join(unhealthy_providers)}. "
                "Consider reviewing their configuration or contacting support."
            )
        
        # Cost analysis
        if self.config.cost_optimization:
            high_cost_providers = [
                name for name, metrics in self.providers_health.items()
                if metrics.cost_per_request > 0.01  # Configurable threshold
            ]
            
            if high_cost_providers:
                recommendations.append(
                    f"💰 High-cost providers detected: {', '.join(high_cost_providers)}. "
                    "Consider optimizing usage or negotiating better rates."
                )
        
        # Performance analysis
        slow_providers = [
            name for name, metrics in self.providers_health.items()
            if metrics.avg_response_time > 5000  # 5 seconds
        ]
        
        if slow_providers:
            recommendations.append(
                f"🐌 Slow providers detected: {', '.join(slow_providers)}. "
                "Consider checking connectivity or changing regions."
            )
        
        # Fallback analysis
        if self.system_stats['fallback_requests'] > self.system_stats['successful_requests'] * 0.1:
            recommendations.append(
                "🔄 High number of fallbacks detected. Review the health of primary providers."
            )
        
        # Circuit breaker analysis
        if self.system_stats['circuit_breaker_trips'] > 0:
            recommendations.append(
                f"⚡ {self.system_stats['circuit_breaker_trips']} circuit breakers tripped. "
                "Some providers may be experiencing issues."
            )
        
        return recommendations or ["✅ System is operating optimally"]
    
    async def check_provider_health(self, provider: str) -> bool:
        """
        Checks the health of a specific provider
        
        Args:
            provider: Name of the provider ('mlx', 'huggingface')
            
        Returns:
            True if the provider is healthy
        """
        try:
            if provider not in self.providers_health:
                # Initialize metrics if they don't exist
                self.providers_health[provider] = HealthMetrics(provider=provider)
                return True  # Assume healthy until proven otherwise
            
            metrics = self.providers_health[provider]
            
            # Health criteria
            is_healthy = (
                metrics.health_status in [ProviderHealth.HEALTHY, ProviderHealth.UNKNOWN] and
                metrics.error_rate < 0.5 and  # Less than 50% error rate
                metrics.consecutive_failures < 5 and  # Less than 5 consecutive failures
                metrics.avg_response_time < 10000  # Less than 10 seconds average
            )
            
            return is_healthy
            
        except Exception as e:
            logger.error("Error checking provider health for %s: %s", provider, e)
            return False
    
    async def select_optimal_provider(
        self, 
        request_type: str, 
        complexity: str = "medium",
        requirements: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Selects the optimal provider based on request type and requirements
        
        Args:
            request_type: Type of request ('sentiment', 'technical', 'visual', etc.)
            complexity: Analysis complexity ('low', 'medium', 'high')
            requirements: Specific requirements (timeout, quality, etc.)
            
        Returns:
            Name of the optimal provider, or None if none are available
        """
        try:
            requirements = requirements or {}
            timeout = requirements.get('timeout', 10)
            
            # Evaluate available providers
            provider_scores = {}
            
            for provider in ['mlx', 'huggingface']:
                # Check if the provider is healthy
                is_healthy = await self.check_provider_health(provider)
                if not is_healthy:
                    continue
                
                metrics = self.providers_health.get(provider)
                if not metrics:
                    continue
                
                # Calculate score based on different factors
                score = 100  # Base score
                
                # Health factor
                if metrics.health_status == ProviderHealth.HEALTHY:
                    score += 20
                elif metrics.health_status == ProviderHealth.DEGRADED:
                    score -= 10
                else:
                    score -= 30
                
                # Speed factor (important for timeout)
                if metrics.avg_response_time > 0:
                    if metrics.avg_response_time < timeout * 1000 * 0.5:  # Less than 50% of timeout
                        score += 15
                    elif metrics.avg_response_time > timeout * 1000:  # More than the timeout
                        score -= 50
                
                # Success factor
                score += metrics.success_rate * 0.3
                
                # Cost factor (MLX is free)
                if provider == 'mlx':
                    score += 10  # Bonus for being free
                
                # Factor specific to request type
                if request_type == 'visual' and provider == 'huggingface':
                    score += 5  # HF is generally better for visual
                elif request_type in ['sentiment', 'technical'] and provider == 'mlx':
                    score += 5  # MLX may be sufficient for simple analyses
                
                # Complexity factor
                if complexity == 'high' and provider == 'huggingface':
                    score += 10  # HF is better for complex tasks
                elif complexity == 'low' and provider == 'mlx':
                    score += 10  # MLX is sufficient for simple tasks
                
                provider_scores[provider] = max(0, score)  # No negative scores
            
            # Select the best provider
            if not provider_scores:
                return None
            
            best_provider = max(provider_scores.items(), key=lambda x: x[1])[0]
            return best_provider
            
        except Exception as e:
            logger.error("Error selecting optimal provider: %s", e)
            return 'mlx'  # Default fallback
    
    async def get_health_summary(self) -> Dict[str, Dict[str, Any]]:
        """
        Gets a health summary for all providers
        
        Returns:
            Dict with health information per provider
        """
        try:
            summary = {}
            
            for provider, metrics in self.providers_health.items():
                is_healthy = await self.check_provider_health(provider)
                
                summary[provider] = {
                    'healthy': is_healthy,
                    'status': metrics.health_status.value,
                    'success_rate': metrics.success_rate,
                    'error_rate': metrics.error_rate,
                    'avg_response_time': metrics.avg_response_time,
                    'total_requests': metrics.total_requests,
                    'consecutive_failures': metrics.consecutive_failures,
                    'last_success': metrics.last_success,
                    'last_failure': metrics.last_failure
                }
            
            # Add providers that are not in metrics but should be
            for provider in ['mlx', 'huggingface']:
                if provider not in summary:
                    summary[provider] = {
                        'healthy': True,
                        'status': 'unknown',
                        'success_rate': 100.0,
                        'error_rate': 0.0,
                        'avg_response_time': 0.0,
                        'total_requests': 0,
                        'consecutive_failures': 0,
                        'last_success': None,
                        'last_failure': None
                    }
            
            return summary
            
        except Exception as e:
            logger.error("Error getting health summary: %s", e)
            return {'error': str(e)}
