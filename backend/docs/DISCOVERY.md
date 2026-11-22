# Descubrimiento de cuentas — 24 sep 2026 (exclusiones al 25 sep 2026)

Obtenido con los MCP (solo lectura). Para actualizarlo usa **Settings → Clientes →
Descubrir cuentas** en el dashboard o `GET /api/accounts/*`.

**Exclusiones**: los nombres que coinciden con `EXCLUDED_CLIENT_PATTERNS`
(`backend/app/exclusions.py`, hoy `vidanta`) no se muestran ni se pueden asignar
en la aplicación, y sus clientes locales fueron borrados del registro. Nada se
tocó en Google Ads, Meta ni Google Analytics: los accesos reales siguen intactos.

## Asociaciones propuestas (cargadas en `data/clients.json`)

| Cliente | Google Ads | Meta Ads | GA4 |
|---|---|---|---|
| Blumia | 6364343858 blumia.mx | 1066569498344996 Blumia.mx | 496398757 blumia.mx |
| Clarvi | 5017039586 Clarvi | 2953260231521252 CLARVI Ads | 480211892 CLARVI |
| Modern Mexico (USD) | 3060413629 (vía MCC Tachuela) | 1008154060848851 | 469261444 |
| Doc Towers | 7525546248 DocTowers (vía MCC) | 872966514132855 DocTowers | 415691013 Doc Towers |
| Sign Factory | 8225398841 Sign Factory 2022 (vía MCC) | 26322709467360690 | 264939271 |
| Grupo Regio | 7493340758 | 1619893934837515 | **pendiente** |
| Jungala | 5267196729 | 802869966755765 | **pendiente** |
| Ssiento | 2183992705 SSIENTO.COM (vía MCC) | 1465882777215540 Ssiento publicitaria | **pendiente** |
| Dorado Center | 3781161137 | 2138091603358475 | — (no existe) |
| Tachuela Marketing | 7852601280 Tachuela Growth Marketing (vía MCC) | 431870981552410 | **pendiente** |
| Bambino | 5276108056 Corporativo Bambino | **pendiente** | 440407831 grupobambino.com |
| Santuario Residencial | 8323928147 | — | **pendiente** |
| Cirque du Soleil JOYÀ | 6224083996 MarktetingJOYÀ (vía MCC) | **pendiente** | **pendiente** |
| iupi.mx | — | 787769601636092 | 504404740 IUPI.MX |
| Maternalia | — | 1299299048505394 | 539483266 |
| The View | — (3781931339 cancelada) | 585352203499122 (UNSETTLED) | 353558692 theview.mx |
| Vida Vacations | — | **pendiente** | — |

## Asociaciones ambiguas (no asumidas)

- **Grupo Regio · GA4**: la cuenta tiene México, Estados Unidos (Miami), República Dominicana, varias web2print; además existe "Marriott Grupo Regio".
- **Jungala · GA4**: "Jungala Aqua Experience GA4" (417071493), "Jungala QR" (376626691), "jungala-aqua-experience-prod" (540652316).
- **Ssiento · GA4**: una propiedad por ciudad (Nacional, Mty, Saltillo, CDMX, León, Querétaro, Chih, USA, San Diego) + "Ssiento - GA4 - GLOBAL MX".
- **Tachuela · GA4**: "Tachuela.mx - GA4" (390438039), "Tachuela GA4" (299619800), "Swydo - Tachuela Marketing".
- **Bambino · Meta**: "Casas Bambino" (441970143809585) está en otro negocio; confirmar si es el mismo cliente que "Corporativo Bambino".
- **Santuario Residencial · GA4**: posible "santuarioveracruz.com" (479949313).
- **Cirque du Soleil JOYÀ · Meta**: "Cirque du Soleil JOYÀ" (2048742145193546) vs "JOYÀ_1 (Read-Only)" (1082598430772317) vs "JOYÀ (Read-Only)" (977951765062587, sin MCP).
  **GA4**: "Cirque Du Soleil JOYA QR" (376640657).
- **Vida Vacations · Meta**: "Vida Vacations MX" (781558888969462) y "Vida Vacations" (575778389771828).

## Cuentas sin cliente asignado

- **Google Ads**: MCC Tachuela (7954113535), TORRE AURELIO (8651572423);
  sin acceso/deshabilitadas: 3781931339 (The View MX), 8973275615 (Bambino), 3373364854; ~27 cuentas canceladas bajo el MCC.
- **Meta**: Nikte ads-logar, Consume, Descubre Peñasco MX, Cirque du Soleil LUDÕ, Brand META Cartier USD,
  The Reserve (sin MCP), Lili Ruiz (x3), Hotel Depot (DISABLED), Macao Cancun (DISABLED), Joyá by Cirque du Soleil (CLOSED).
- **GA4**: Invertikal Developers (7 propiedades), Macao Casino, La Salle Cancún, Xcaret Info Parks, Laberinto Xcaret,
  Grupo Xcaret Booklet, Kai Restaurante, BVG Cancún, Marriott Grupo Regio, CDS.com, Demo Account.
