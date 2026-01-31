"""
TradingView Scraper - Sistema de captura de charts y scraping de indicadores

Características:
- Login persistente con sesión guardada
- Captura de charts con indicadores
- Scraping de código fuente de indicadores públicos
- Biblioteca de indicadores descargados
"""

import asyncio
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict
from enum import Enum
import logging

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==================== Configuración ====================

@dataclass
class TradingViewCredentials:
    """Credenciales para TradingView"""
    username: str
    password: str
    
    @classmethod
    def from_env(cls) -> Optional["TradingViewCredentials"]:
        """Cargar credenciales desde variables de entorno"""
        username = os.getenv("TRADINGVIEW_USERNAME")
        password = os.getenv("TRADINGVIEW_PASSWORD")
        
        if username and password:
            return cls(username=username, password=password)
        return None
    
    @classmethod
    def from_file(cls, path: str = "config/tradingview_credentials.json") -> Optional["TradingViewCredentials"]:
        """Cargar credenciales desde archivo"""
        try:
            with open(path) as f:
                data = json.load(f)
                return cls(username=data["username"], password=data["password"])
        except:
            return None


@dataclass
class ScrapedIndicator:
    """Indicador scrapeado de TradingView"""
    name: str
    author: str
    description: str
    pine_script: str
    version: int
    url: str
    likes: int
    scraped_at: str
    tags: List[str]


class TradingViewScraper:
    """
    Scraper de TradingView con soporte para:
    - Login y sesión persistente
    - Captura de charts
    - Extracción de código de indicadores públicos
    """
    
    STORAGE_DIR = Path("cache/tradingview")
    SESSION_FILE = STORAGE_DIR / "session.json"
    INDICATORS_DIR = STORAGE_DIR / "indicators"
    SCREENSHOTS_DIR = STORAGE_DIR / "screenshots"
    
    def __init__(self, credentials: Optional[TradingViewCredentials] = None):
        self.credentials = credentials or TradingViewCredentials.from_env() or TradingViewCredentials.from_file()
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._setup_dirs()
    
    def _setup_dirs(self):
        """Crear directorios necesarios"""
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.INDICATORS_DIR.mkdir(exist_ok=True)
        self.SCREENSHOTS_DIR.mkdir(exist_ok=True)
    
    async def start(self, headless: bool = True):
        """Iniciar navegador"""
        playwright = await async_playwright().start()
        
        self.browser = await playwright.chromium.launch(
            headless=headless,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        
        # Intentar restaurar sesión
        if self.SESSION_FILE.exists():
            logger.info("📁 Restaurando sesión guardada...")
            self.context = await self.browser.new_context(
                storage_state=str(self.SESSION_FILE),
                viewport={'width': 1920, 'height': 1080}
            )
        else:
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080}
            )
        
        self.page = await self.context.new_page()
        
        # Verificar si estamos logueados
        if await self._is_logged_in():
            logger.info("✅ Sesión activa - ya logueado")
        elif self.credentials:
            logger.info("🔐 Iniciando sesión...")
            await self._login()
        else:
            logger.warning("⚠️ Sin credenciales - modo anónimo")
    
    async def stop(self):
        """Cerrar navegador y guardar sesión"""
        if self.context:
            # Guardar estado de sesión
            await self.context.storage_state(path=str(self.SESSION_FILE))
            logger.info("💾 Sesión guardada")
        
        if self.browser:
            await self.browser.close()
    
    async def _is_logged_in(self) -> bool:
        """Verificar si hay sesión activa"""
        try:
            await self.page.goto("https://www.tradingview.com/", timeout=30000)
            await self.page.wait_for_load_state("networkidle", timeout=10000)
            
            # Buscar botón de usuario (indica sesión activa)
            user_menu = await self.page.query_selector('[data-name="user-menu-button"]')
            return user_menu is not None
        except:
            return False
    
    async def _login(self):
        """Hacer login en TradingView"""
        if not self.credentials:
            logger.error("❌ No hay credenciales configuradas")
            return False
        
        try:
            # Ir a página de login
            await self.page.goto("https://www.tradingview.com/accounts/signin/", timeout=30000)
            await self.page.wait_for_load_state("networkidle")
            
            # Click en "Email"
            email_button = await self.page.wait_for_selector('button:has-text("Email")', timeout=5000)
            if email_button:
                await email_button.click()
                await asyncio.sleep(1)
            
            # Llenar credenciales
            await self.page.fill('input[name="id_username"]', self.credentials.username)
            await self.page.fill('input[name="id_password"]', self.credentials.password)
            
            # Click en Sign in
            await self.page.click('button[type="submit"]')
            
            # Esperar redirección
            await self.page.wait_for_load_state("networkidle", timeout=30000)
            
            # Verificar login exitoso
            if await self._is_logged_in():
                logger.info("✅ Login exitoso")
                await self.context.storage_state(path=str(self.SESSION_FILE))
                return True
            else:
                logger.error("❌ Login falló")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error en login: {e}")
            return False
    
    async def capture_chart(
        self,
        symbol: str = "BTCUSDT",
        exchange: str = "BINANCE",
        interval: str = "1H",
        indicators: List[str] = None,
        save_path: Optional[str] = None
    ) -> Optional[str]:
        """
        Capturar screenshot de chart con indicadores
        
        Args:
            symbol: Par de trading (ej: BTCUSDT)
            exchange: Exchange (ej: BINANCE, BYBIT)
            interval: Timeframe (ej: 1, 5, 15, 60, 240, D, W)
            indicators: Lista de indicadores a aplicar
            save_path: Ruta para guardar el screenshot
            
        Returns:
            Ruta del screenshot guardado
        """
        try:
            # Construir URL del chart
            chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}&interval={interval}"
            
            await self.page.goto(chart_url, timeout=60000)
            await self.page.wait_for_load_state("networkidle", timeout=30000)
            
            # Esperar a que el chart cargue
            await asyncio.sleep(3)
            
            # Ocultar popups y banners
            await self._hide_overlays()
            
            # Capturar screenshot
            if not save_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = str(self.SCREENSHOTS_DIR / f"{symbol}_{interval}_{timestamp}.png")
            
            await self.page.screenshot(path=save_path, full_page=False)
            logger.info(f"📸 Chart capturado: {save_path}")
            
            return save_path
            
        except Exception as e:
            logger.error(f"❌ Error capturando chart: {e}")
            return None
    
    async def _hide_overlays(self):
        """Ocultar popups y overlays"""
        try:
            # Cerrar cualquier popup
            close_buttons = await self.page.query_selector_all('[data-name="close"]')
            for btn in close_buttons:
                try:
                    await btn.click()
                except:
                    pass
            
            # Ocultar banners con CSS
            await self.page.add_style_tag(content="""
                .tv-dialog, .tv-tooltip, .toast-container, 
                [class*="popup"], [class*="banner"], [class*="overlay"] {
                    display: none !important;
                }
            """)
        except:
            pass
    
    async def scrape_indicator(self, indicator_url: str) -> Optional[ScrapedIndicator]:
        """
        Scrapear código fuente de un indicador público
        
        Args:
            indicator_url: URL del indicador en TradingView
            
        Returns:
            ScrapedIndicator con el código y metadata
        """
        try:
            await self.page.goto(indicator_url, timeout=60000)
            await self.page.wait_for_load_state("networkidle")
            
            # Esperar a que cargue el contenido
            await asyncio.sleep(2)
            
            # Extraer nombre
            name_elem = await self.page.query_selector('h1')
            name = await name_elem.inner_text() if name_elem else "Unknown"
            
            # Extraer autor
            author_elem = await self.page.query_selector('[class*="author"]')
            author = await author_elem.inner_text() if author_elem else "Unknown"
            
            # Extraer descripción
            desc_elem = await self.page.query_selector('[class*="description"]')
            description = await desc_elem.inner_text() if desc_elem else ""
            
            # Intentar obtener el código fuente
            # Método 1: Botón "Source code" si existe
            source_button = await self.page.query_selector('button:has-text("Source code")')
            if source_button:
                await source_button.click()
                await asyncio.sleep(1)
            
            # Buscar el código Pine Script
            code_elem = await self.page.query_selector('pre, code, [class*="pine-script"], .tv-script-code')
            
            pine_script = ""
            if code_elem:
                pine_script = await code_elem.inner_text()
            
            # Si no encontramos código, intentar desde el editor
            if not pine_script:
                # Intentar abrir en el chart y extraer desde ahí
                pass
            
            # Extraer likes
            likes_elem = await self.page.query_selector('[class*="likes"]')
            likes_text = await likes_elem.inner_text() if likes_elem else "0"
            likes = int(re.sub(r'\D', '', likes_text) or 0)
            
            # Detectar versión de Pine Script
            version = 5
            if "//@version=" in pine_script:
                match = re.search(r'//@version=(\d+)', pine_script)
                if match:
                    version = int(match.group(1))
            
            indicator = ScrapedIndicator(
                name=name.strip(),
                author=author.strip(),
                description=description.strip()[:500],
                pine_script=pine_script,
                version=version,
                url=indicator_url,
                likes=likes,
                scraped_at=datetime.now().isoformat(),
                tags=[]
            )
            
            # Guardar a archivo
            safe_name = re.sub(r'[^\w\-]', '_', name.strip())[:50]
            save_path = self.INDICATORS_DIR / f"{safe_name}.json"
            
            with open(save_path, 'w') as f:
                json.dump(asdict(indicator), f, indent=2)
            
            logger.info(f"✅ Indicador scrapeado: {name}")
            return indicator
            
        except Exception as e:
            logger.error(f"❌ Error scrapeando indicador: {e}")
            return None
    
    async def search_indicators(
        self,
        query: str,
        category: str = "all",
        max_results: int = 20
    ) -> List[Dict[str, str]]:
        """
        Buscar indicadores en TradingView
        
        Args:
            query: Término de búsqueda
            category: Categoría (all, oscillators, volatility, trend, etc.)
            max_results: Máximo número de resultados
            
        Returns:
            Lista de diccionarios con {name, url, author, likes}
        """
        try:
            search_url = f"https://www.tradingview.com/scripts/search/{query}/"
            await self.page.goto(search_url, timeout=60000)
            await self.page.wait_for_load_state("networkidle")
            
            await asyncio.sleep(2)
            
            results = []
            
            # Buscar cards de indicadores
            cards = await self.page.query_selector_all('[class*="card"]')
            
            for card in cards[:max_results]:
                try:
                    # Extraer info
                    link = await card.query_selector('a')
                    if link:
                        href = await link.get_attribute('href')
                        text = await link.inner_text()
                        
                        results.append({
                            'name': text.strip(),
                            'url': f"https://www.tradingview.com{href}" if href.startswith('/') else href,
                            'author': 'Unknown',
                            'likes': 0
                        })
                except:
                    continue
            
            logger.info(f"🔍 Encontrados {len(results)} indicadores para '{query}'")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error buscando indicadores: {e}")
            return []
    
    async def batch_scrape(self, indicator_urls: List[str]) -> List[ScrapedIndicator]:
        """
        Scrapear múltiples indicadores
        
        Args:
            indicator_urls: Lista de URLs de indicadores
            
        Returns:
            Lista de indicadores scrapeados exitosamente
        """
        results = []
        
        for i, url in enumerate(indicator_urls):
            logger.info(f"📥 Scrapeando {i+1}/{len(indicator_urls)}: {url}")
            
            indicator = await self.scrape_indicator(url)
            if indicator:
                results.append(indicator)
            
            # Rate limiting
            await asyncio.sleep(2)
        
        logger.info(f"✅ Scrapeados {len(results)}/{len(indicator_urls)} indicadores")
        return results


# ==================== Interfaz CLI ====================

async def main():
    """Demo del scraper"""
    import argparse
    
    parser = argparse.ArgumentParser(description="TradingView Scraper")
    parser.add_argument("--headless", action="store_true", help="Modo headless")
    parser.add_argument("--chart", type=str, help="Capturar chart (ej: BTCUSDT)")
    parser.add_argument("--search", type=str, help="Buscar indicadores")
    parser.add_argument("--scrape", type=str, help="Scrapear indicador (URL)")
    
    args = parser.parse_args()
    
    scraper = TradingViewScraper()
    
    try:
        await scraper.start(headless=args.headless)
        
        if args.chart:
            path = await scraper.capture_chart(symbol=args.chart)
            print(f"Chart guardado: {path}")
        
        elif args.search:
            results = await scraper.search_indicators(args.search)
            print(f"\n📊 Resultados para '{args.search}':")
            for r in results[:10]:
                print(f"  • {r['name']}: {r['url']}")
        
        elif args.scrape:
            indicator = await scraper.scrape_indicator(args.scrape)
            if indicator:
                print(f"\n✅ Indicador: {indicator.name}")
                print(f"   Autor: {indicator.author}")
                print(f"   Versión: v{indicator.version}")
                print(f"   Código: {len(indicator.pine_script)} caracteres")
        
        else:
            # Demo por defecto
            print("🚀 TradingView Scraper Demo")
            print("="*50)
            
            # Buscar indicadores populares
            print("\n🔍 Buscando indicadores de 'swing failure'...")
            results = await scraper.search_indicators("swing failure pattern", max_results=5)
            
            for r in results:
                print(f"  • {r['name']}")
            
            # Capturar chart
            print("\n📸 Capturando chart BTCUSDT...")
            path = await scraper.capture_chart("BTCUSDT", interval="60")
            print(f"  Guardado: {path}")
    
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
