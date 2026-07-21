#!/usr/bin/env python3
"""
Captura de TradingView usando Playwright (versión integrada)
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)

SESSION_FILE = Path("tradingview_session_state.json")

class TradingViewPlaywrightCapture:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.screenshots_dir = Path("screenshots")
        self.screenshots_dir.mkdir(exist_ok=True)
        self.playwright = None
        self.browser = None
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright no disponible. Instala con: pip install playwright && playwright install")
        if not SESSION_FILE.exists():
            logger.warning(f"⚠️ Archivo de sesión no encontrado: {SESSION_FILE}")
        else:
            logger.info(f"✅ Archivo de sesión encontrado: {SESSION_FILE}")

    async def __aenter__(self):
        logger.info("🚀 Inicializando Playwright y el navegador (una sola vez)...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        logger.info("✅ Navegador listo.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.browser and self.browser.is_connected():
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("🔒 Navegador y Playwright cerrados.")

    async def capture_chart_from_url(self, url: str, timeframe: str, symbol: str) -> bytes | None:
        if not self.browser:
            raise RuntimeError("El navegador no está inicializado. Usa 'async with TradingViewPlaywrightCapture() as capture:' .")
        context = None
        page = None
        try:
            # Usar la nueva URL fija si no se pasa otra
            url = "https://es.tradingview.com/chart/iERzAcI8/"
            logger.info(f"📸 Capturando {timeframe} desde URL: {url}")
            storage_state = None
            context_params = {
                'viewport': {'width': 1400, 'height': 900},
                'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
            }
            if SESSION_FILE.exists() and SESSION_FILE.stat().st_size > 0:
                try:
                    with open(SESSION_FILE) as f:
                        storage_state = json.load(f)
                    logger.info("🔄 Usando sesión guardada para el nuevo contexto.")
                    context = await self.browser.new_context(storage_state=storage_state, **context_params)
                except Exception as e:
                    logger.error(f"❌ Error leyendo o usando sesión: {e}. Se usará contexto limpio.")
                    context = await self.browser.new_context(**context_params)
            else:
                logger.info("🔄 Creando contexto limpio (sin sesión).")
                context = await self.browser.new_context(**context_params)
            page = await context.new_page()
            logger.info("📄 Nueva página creada en el contexto.")
            page.set_default_timeout(90000)
            logger.info(f" Navegando al gráfico específico: {url}")
            response = await page.goto(url, timeout=90000, wait_until="domcontentloaded")
            if response:
                logger.info(f"✅ Gráfico cargado: {response.status}")
                if response.status != 200:
                    logger.warning(f"⚠️ Status code: {response.status}")
                    return None
            else:
                logger.warning("⚠️ No se recibió respuesta del gráfico")
                return None
            logger.info(" Simulando interacción de usuario para renderizado...")
            viewport_size = page.viewport_size
            if viewport_size:
                await page.mouse.move(viewport_size['width'] / 2, viewport_size['height'] / 2)
                await page.mouse.click(viewport_size['width'] / 2, viewport_size['height'] / 2)

            # Esperar más tiempo para la carga inicial
            await asyncio.sleep(2)

            # Esperar a que aparezcan elementos del gráfico
            await page.wait_for_selector('canvas, [class*="chart"]', timeout=45000)
            logger.info("✅ Elementos del gráfico detectados.")

            # Cerrar publicidad antes de ocultar elementos UI
            await self._close_advertisements(page)

            # Esperar un poco más para que se cierre completamente cualquier modal
            await asyncio.sleep(2)

            # Ocultar elementos UI
            await self._hide_ui_elements(page)

            # Esperar a que el gráfico se renderice completamente
            logger.info("⏳ Esperando a que el gráfico se renderice completamente...")

            # Verificar que hay datos en el gráfico usando JavaScript
            chart_ready = False
            max_attempts = 10
            attempt = 0

            while not chart_ready and attempt < max_attempts:
                try:
                    chart_ready = await page.evaluate("""
                        () => {
                            // Buscar canvas del gráfico
                            const canvases = document.querySelectorAll('canvas');
                            if (canvases.length === 0) return false;

                            // Verificar que hay contenido en el canvas
                            for (let canvas of canvases) {
                                const ctx = canvas.getContext('2d');
                                if (ctx) {
                                    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                                    const data = imageData.data;

                                    // Verificar que no es solo transparente/blanco
                                    let hasContent = false;
                                    for (let i = 0; i < data.length; i += 4) {
                                        const r = data[i];
                                        const g = data[i + 1];
                                        const b = data[i + 2];
                                        const a = data[i + 3];

                                        // Si encontramos píxeles que no son blancos/transparentes
                                        if (a > 0 && (r < 250 || g < 250 || b < 250)) {
                                            hasContent = true;
                                            break;
                                        }
                                    }

                                    if (hasContent) return true;
                                }
                            }

                            return false;
                        }
                    """)

                    if chart_ready:
                        logger.info("✅ Gráfico renderizado con contenido detectado")
                        break
                    else:
                        logger.info(f"⏳ Esperando renderizado del gráfico... (intento {attempt + 1}/{max_attempts})")
                        await asyncio.sleep(1)
                        attempt += 1

                except Exception as e:
                    logger.debug(f"Error verificando renderizado: {e}")
                    await asyncio.sleep(1)
                    attempt += 1

            if not chart_ready:
                logger.warning("⚠️ El gráfico puede no estar completamente renderizado")

            # Esperar adicional para estabilización
            await asyncio.sleep(2)
            screenshot_bytes = await page.screenshot(type='png', full_page=False)
            if len(screenshot_bytes) < 5000:
                logger.error(f"❌ Screenshot muy pequeño ({len(screenshot_bytes)} bytes) - probablemente vacío")
                return None
            # Guardar screenshot en carpeta para auditoría
            from datetime import datetime
            ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')
            symbol_str = symbol if symbol else 'unknown'
            filename = f"{ts}_{symbol_str}_{timeframe}.png"
            screenshot_path = self.screenshots_dir / filename
            try:
                with open(screenshot_path, 'wb') as f:
                    f.write(screenshot_bytes)
                logger.info(f"🖼️ Screenshot guardado en: {screenshot_path}")
            except Exception as e:
                logger.error(f"❌ Error guardando screenshot: {e}")
            return screenshot_bytes
        except Exception as e:
            logger.error(f"❌ Error capturando {timeframe}: {e}")
            return None
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def _close_advertisements(self, page):
        """Detecta y cierra publicidad/modales en TradingView"""
        try:
            logger.info("🚫 Buscando y cerrando publicidad...")

            # Lista de selectores comunes para botones de cerrar publicidad
            close_selectors = [
                # Botón X genérico
                '[data-name="close"]',
                '[aria-label="Close"]',
                '[aria-label="Cerrar"]',
                'button[class*="close"]',
                '.close-button',
                '.modal-close',
                '.popup-close',

                # Selectores específicos de TradingView
                '.tv-dialog__close',
                '.tv-popup__close',
                '.tv-modal__close',
                '[data-role="button"][aria-label*="close"]',
                '[data-role="button"][aria-label*="Close"]',
                '[data-role="button"][aria-label*="cerrar"]',
                '[data-role="button"][aria-label*="Cerrar"]',

                # Selectores para modales de promoción/publicidad
                '.promo-popup .close',
                '.advertisement .close',
                '.banner .close',
                '.offer-modal .close',

                # Selectores específicos para popup de LATAM sale y similares
                'div[style*="position: fixed"] button',
                'div[style*="z-index"] button[style*="position: absolute"]',
                'div[class*="sale"] button',
                'div[class*="promo"] button',
                'div[class*="offer"] button',
                'div[class*="discount"] button',

                # Selectores más específicos basados en la estructura común de TradingView
                'div[class*="dialog"] button[class*="close"]',
                'div[class*="modal"] button[class*="close"]',
                'div[class*="popup"] button[class*="close"]',

                # Selector para el botón X en la esquina superior derecha
                'button:has-text("×")',
                'button:has-text("✕")',
                'span:has-text("×")',
                'span:has-text("✕")',

                # Selectores CSS más amplios para elementos que contengan X
                '[role="button"]:has-text("×")',
                '[role="button"]:has-text("✕")',

                # Selectores específicos para overlay de publicidad
                'div[style*="position: fixed"][style*="top: 0"] button',
                'div[style*="position: fixed"][style*="left: 0"] button',
                'div[style*="background"] button[style*="top"]',
            ]

            # Intentar cerrar cualquier modal/publicidad visible
            for selector in close_selectors:
                try:
                    # Buscar elementos visibles que coincidan con el selector
                    elements = await page.query_selector_all(selector)
                    for element in elements:
                        # Verificar si el elemento es visible
                        is_visible = await element.is_visible()
                        if is_visible:
                            logger.info(f"🎯 Encontrado botón de cerrar: {selector}")
                            await element.click()
                            logger.info(f"✅ Publicidad cerrada usando selector: {selector}")
                            # Esperar un poco para que se cierre el modal
                            await asyncio.sleep(0.5)
                            break
                except Exception as e:
                    # Continuar con el siguiente selector si este falla
                    continue

            # Método alternativo: buscar por texto específico en botones
            try:
                # Buscar botones que contengan texto de cerrar
                close_texts = ["×", "✕", "Close", "Cerrar", "X"]
                for text in close_texts:
                    try:
                        close_button = await page.query_selector(f'button:has-text("{text}")')
                        if close_button and await close_button.is_visible():
                            logger.info(f"🎯 Encontrado botón con texto: {text}")
                            await close_button.click()
                            logger.info(f"✅ Publicidad cerrada usando texto: {text}")
                            await asyncio.sleep(0.5)
                            break
                    except:
                        continue
            except Exception as e:
                logger.debug(f"Método de texto falló: {e}")

            # Método adicional: buscar elementos con posición típica de botón cerrar (esquina superior derecha)
            try:
                # Ejecutar JavaScript para encontrar elementos en la esquina superior derecha
                js_code = """
                () => {
                    const elements = document.querySelectorAll('*');
                    const closeButtons = [];
                    const modalOverlays = [];

                    elements.forEach(el => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);

                        // Buscar overlays de modal (fondo oscuro/transparente que cubre toda la pantalla)
                        if (style.position === 'fixed' && 
                            rect.width > window.innerWidth * 0.8 && 
                            rect.height > window.innerHeight * 0.8 &&
                            (style.backgroundColor.includes('rgba') || style.background.includes('rgba'))) {
                            modalOverlays.push(el);
                        }

                        // Buscar elementos en la esquina superior derecha que podrían ser botones de cerrar
                        if (rect.right > window.innerWidth - 100 && 
                            rect.top < 100 && 
                            (el.textContent.includes('×') || 
                             el.textContent.includes('✕') || 
                             el.textContent.includes('X') ||
                             el.getAttribute('aria-label')?.toLowerCase().includes('close') ||
                             el.getAttribute('aria-label')?.toLowerCase().includes('cerrar') ||
                             el.className.toLowerCase().includes('close'))) {
                            closeButtons.push(el);
                        }

                        // Buscar botones dentro de elementos con position fixed (popups)
                        if (style.position === 'fixed' && el.tagName === 'BUTTON') {
                            const parentRect = el.parentElement?.getBoundingClientRect();
                            if (parentRect && parentRect.width > 300 && parentRect.height > 200) {
                                // Es probable que sea un botón en un popup
                                if (el.textContent.includes('×') || 
                                    el.textContent.includes('✕') || 
                                    el.textContent.includes('X') ||
                                    el.className.toLowerCase().includes('close') ||
                                    rect.right > parentRect.right - 50) { // Botón en esquina derecha del popup
                                    closeButtons.push(el);
                                }
                            }
                        }

                        // Buscar elementos que contengan texto relacionado con ofertas/promociones
                        if (style.position === 'fixed' && 
                            (el.textContent.toLowerCase().includes('sale') ||
                             el.textContent.toLowerCase().includes('descuento') ||
                             el.textContent.toLowerCase().includes('oferta') ||
                             el.textContent.toLowerCase().includes('promo'))) {
                            // Buscar botón de cerrar dentro de este elemento
                            const closeBtn = el.querySelector('button, [role="button"], .close, [data-name="close"]');
                            if (closeBtn) {
                                closeButtons.push(closeBtn);
                            }
                        }
                    });

                    // Si encontramos overlays de modal, intentar cerrarlos haciendo clic
                    modalOverlays.forEach(overlay => {
                        if (overlay.style.zIndex > 1000) {
                            closeButtons.push(overlay);
                        }
                    });

                    return closeButtons;
                }
                """

                close_buttons = await page.evaluate(js_code)
                if close_buttons:
                    logger.info(f"🎯 Encontrados {len(close_buttons)} posibles botones de cerrar por posición")
                    # Hacer clic en el primer botón encontrado
                    await page.evaluate("arguments[0].click()", close_buttons[0])
                    logger.info("✅ Publicidad cerrada usando detección por posición")
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.debug(f"Método de JavaScript falló: {e}")

            # Método adicional: usar tecla ESC para cerrar modales
            try:
                logger.info("⌨️ Intentando cerrar popup con tecla ESC...")
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.5)
                # Intentar ESC múltiples veces por si hay varios modales
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.3)
                logger.info("✅ Tecla ESC enviada para cerrar popups")
            except Exception as e:
                logger.debug(f"Método de ESC falló: {e}")

            # Método final: forzar cierre de elementos con alta z-index
            try:
                logger.info("🔨 Método agresivo: ocultando elementos con z-index alto...")
                await page.evaluate("""
                    () => {
                        const allElements = document.querySelectorAll('*');
                        let hiddenCount = 0;

                        allElements.forEach(el => {
                            const style = window.getComputedStyle(el);
                            const zIndex = parseInt(style.zIndex);

                            // Ocultar elementos con z-index muy alto que probablemente sean popups
                            if (zIndex > 9999 || 
                                (style.position === 'fixed' && zIndex > 1000)) {
                                el.style.display = 'none';
                                el.style.visibility = 'hidden';
                                el.style.opacity = '0';
                                hiddenCount++;
                            }

                            // Ocultar elementos que contengan texto de ofertas/promociones
                            if (el.textContent && 
                                (el.textContent.toLowerCase().includes('latam sale') ||
                                 el.textContent.toLowerCase().includes('descuento') ||
                                 el.textContent.toLowerCase().includes('oferta') ||
                                 el.textContent.toLowerCase().includes('no se pierda'))) {
                                el.style.display = 'none';
                                el.style.visibility = 'hidden';
                                hiddenCount++;
                            }
                        });

                        return hiddenCount;
                    }
                """)
                logger.info("✅ Elementos de alta prioridad ocultados forzosamente")
            except Exception as e:
                logger.debug(f"Método agresivo falló: {e}")

            logger.info("🔍 Búsqueda de publicidad completada")

        except Exception as e:
            logger.warning(f"Error cerrando publicidad: {e}")

    async def _hide_ui_elements(self, page):
        try:
            css = """
            .tv-dialog, .tv-toast, .tv-screener-popup, .tv-floating-toolbar {
                display: none !important;
                visibility: hidden !important;
            }
            .tv-lightweight-charts__watermark, .watermark, .tv-chart-view__watermark {
                display: none !important;
                visibility: hidden !important;
            }
            .layout__area--bottom {
                 display: none !important;
                 visibility: hidden !important;
            }
            /* Ocultar cualquier modal o publicidad restante */
            .tv-dialog-modal, .tv-popup, .tv-modal, .promo-popup, .advertisement, .banner, .offer-modal {
                display: none !important;
                visibility: hidden !important;
            }
            /* Ocultar overlays */
            .tv-dialog-modal__overlay, .tv-popup__overlay, .modal-overlay {
                display: none !important;
                visibility: hidden !important;
            }
            /* Selectores específicos para popups de ofertas/promociones */
            div[style*="position: fixed"][style*="z-index"] {
                display: none !important;
                visibility: hidden !important;
            }
            /* Ocultar elementos que contengan texto de ofertas */
            *:contains("LATAM sale"), *:contains("descuento"), *:contains("oferta"), 
            *:contains("No se pierda"), *:contains("sale"), *:contains("promo") {
                display: none !important;
                visibility: hidden !important;
            }
            /* Ocultar elementos con z-index muy alto (típicos de popups) */
            *[style*="z-index: 999"], *[style*="z-index: 9999"], *[style*="z-index: 99999"] {
                display: none !important;
                visibility: hidden !important;
            }
            /* Ocultar elementos con fondo semi-transparente (overlays) */
            div[style*="background: rgba"], div[style*="background-color: rgba"] {
                display: none !important;
                visibility: hidden !important;
            }
            """
            await page.add_style_tag(content=css)
        except Exception as e:
            logger.warning(f"No se pudieron ocultar elementos UI: {e}")

# Función asíncrona para obtener el path del PNG guardado
async def get_chart_path_async(url: str, timeframe: str, symbol: str):
    """Versión asíncrona para obtener el path del chart"""
    # Validar que symbol y timeframe sean cadenas no vacías
    if not symbol or not isinstance(symbol, str):
        logger.error("El parámetro 'symbol' es obligatorio y debe ser una cadena no vacía.")
        return None
    if not timeframe or not isinstance(timeframe, str):
        logger.error("El parámetro 'timeframe' es obligatorio y debe ser una cadena no vacía.")
        return None
    if not url or not isinstance(url, str):
        logger.error("El parámetro 'url' es obligatorio y debe ser una cadena no vacía.")
        return None

    async with TradingViewPlaywrightCapture() as capture:
        img_bytes = await capture.capture_chart_from_url(url, timeframe, symbol)
        if img_bytes:
            # Buscar el archivo más reciente en screenshots
            screenshots_dir = Path("screenshots")
            screenshots = sorted(screenshots_dir.glob(f"*{symbol}*{timeframe}*.png"), reverse=True)
            if screenshots:
                return str(screenshots[0])
        return None

# Función síncrona para obtener el path del PNG guardado
def get_chart_path(url: str, timeframe: str, symbol: str):
    """Versión síncrona para obtener el path del chart"""
    # Validar que symbol y timeframe sean cadenas no vacías
    if not symbol or not isinstance(symbol, str):
        logger.error("El parámetro 'symbol' es obligatorio y debe ser una cadena no vacía.")
        return None
    if not timeframe or not isinstance(timeframe, str):
        logger.error("El parámetro 'timeframe' es obligatorio y debe ser una cadena no vacía.")
        return None
    if not url or not isinstance(url, str):
        logger.error("El parámetro 'url' es obligatorio y debe ser una cadena no vacía.")
        return None

    try:
        # Verificar si ya hay un event loop corriendo
        try:
            loop = asyncio.get_running_loop()
            logger.warning("Event loop ya está corriendo. Usa get_chart_path_async() en su lugar.")
            return None
        except RuntimeError:
            # No hay event loop, podemos usar asyncio.run()
            pass

        async def _get():
            async with TradingViewPlaywrightCapture() as capture:
                img_bytes = await capture.capture_chart_from_url(url, timeframe, symbol)
                if img_bytes:
                    # Buscar el archivo más reciente en screenshots
                    screenshots_dir = Path("screenshots")
                    screenshots = sorted(screenshots_dir.glob(f"*{symbol}*{timeframe}*.png"), reverse=True)
                    if screenshots:
                        return str(screenshots[0])
                return None

        return asyncio.run(_get())
    except Exception as e:
        logger.error(f"Error en get_chart_path: {e}")
        return None
