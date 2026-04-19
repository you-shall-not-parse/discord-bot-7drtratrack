import * as THREE from 'three';
    import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
    import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
    import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

    const MAP_LAYOUT_WIDTH = 2800;
    const MAP_LAYOUT_HEIGHT = 1900;

    const FRONT_LAYOUT = {
      'Omaha Beach Warfare': { region: 'Normandy', x: 1040, y: 690 },
      'Utah Beach Warfare': { region: 'Normandy', x: 960, y: 640 },
      'Carentan Warfare': { region: 'Normandy', x: 1015, y: 605 },
      'Carentan Warfare (Night)': { region: 'Normandy', x: 1045, y: 565 },
      'St. Mere Eglise Warfare': { region: 'Normandy', x: 935, y: 575 },
      'St. Marie Du Mont Warfare': { region: 'Normandy', x: 885, y: 620 },
      'Purple Heart Lane Warfare (Rain)': { region: 'Normandy', x: 1110, y: 635 },
      'Mortain Warfare (Dusk)': { region: 'Normandy', x: 1095, y: 725 },
      'Foy Warfare': { region: 'Ardennes', x: 1295, y: 560 },
      'Elsenborn Ridge Warfare (Dawn)': { region: 'Ardennes', x: 1355, y: 500 },
      'Hill 400 Warfare': { region: 'Rhineland', x: 1420, y: 610 },
      'Hurtgen Forest Warfare': { region: 'Rhineland', x: 1460, y: 565 },
      'Remagen Warfare': { region: 'Rhineland', x: 1505, y: 700 },
      'Driel Warfare': { region: 'Low Countries', x: 1380, y: 390 },
      'Kursk Warfare': { region: 'Eastern Front', x: 2150, y: 600 },
      'Kharkov Warfare': { region: 'Eastern Front', x: 2260, y: 730 },
      'Stalingrad Warfare': { region: 'Eastern Front', x: 2470, y: 740 },
      'Smolensk Warfare (Dusk)': { region: 'Eastern Front', x: 2080, y: 520 },
      'El Alamein Warfare': { region: 'North Africa', x: 1960, y: 1520 },
      'Tobruk Warfare (Dawn)': { region: 'North Africa', x: 2050, y: 1425 }
    };

    const mapViewport = document.getElementById('map-viewport');
    const mapCanvas = document.getElementById('map-canvas');
    const mapOverlayLayer = document.getElementById('map-overlay-layer');
    const mapLoading = document.getElementById('map-loading');
    const pageRoot = document.querySelector('.page');
    const dashboardView = document.getElementById('dashboard-view');
    const aboutView = document.getElementById('about-view');
    const operationsView = document.getElementById('operations-view');
    const battleMapPage = document.getElementById('battle-map-page');
    const openBattleMapButton = document.getElementById('open-battle-map');
    const desktopHomeButton = document.getElementById('desktop-home-button');
    const desktopAboutButton = document.getElementById('desktop-about-button');
    const desktopOperationsButton = document.getElementById('desktop-operations-button');
    const desktopAboutHomeButton = document.getElementById('desktop-about-home-button');
    const desktopAboutPageButton = document.getElementById('desktop-about-page-button');
    const desktopAboutOperationsButton = document.getElementById('desktop-about-operations-button');
    const desktopAboutMapButton = document.getElementById('desktop-about-map-button');
    const desktopOperationsHomeButton = document.getElementById('desktop-operations-home-button');
    const desktopOperationsAboutButton = document.getElementById('desktop-operations-about-button');
    const desktopOperationsPageButton = document.getElementById('desktop-operations-page-button');
    const desktopOperationsMapButton = document.getElementById('desktop-operations-map-button');
    const desktopMapHomeButton = document.getElementById('desktop-map-home-button');
    const desktopMapAboutButton = document.getElementById('desktop-map-about-button');
    const desktopMapOperationsButton = document.getElementById('desktop-map-operations-button');
    const desktopMapOpenButton = document.getElementById('desktop-map-open-button');
    const returnDashboardButton = document.getElementById('return-dashboard');
    const mobileNavToggleButtons = [
      document.getElementById('mobile-nav-toggle-home'),
      document.getElementById('mobile-nav-toggle-map'),
      document.getElementById('mobile-nav-toggle-about'),
      document.getElementById('mobile-nav-toggle-operations')
    ];
    const mobileNavBackdrop = document.getElementById('mobile-nav-backdrop');
    const mobileNavDrawer = document.getElementById('mobile-nav-drawer');
    const mobileNavCloseButton = document.getElementById('mobile-nav-close');
    const mobileNavHomeButton = document.getElementById('mobile-nav-home');
    const mobileNavAboutButton = document.getElementById('mobile-nav-about');
    const mobileNavOperationsButton = document.getElementById('mobile-nav-operations');
    const mobileNavBattleMapButton = document.getElementById('mobile-nav-battle-map');
    const frontStrip = document.getElementById('front-strip');
    const emptyState = document.getElementById('empty-state');
    const challengeGrid = document.getElementById('challenge-grid');
    const challengeSummary = document.getElementById('challenge-summary');
    const intelPanel = document.querySelector('.intel-panel');
    const intelPanelBackdrop = document.getElementById('intel-panel-backdrop');
    const intelPanelCloseButton = document.getElementById('intel-panel-close');
    const statusPill = document.getElementById('status-pill');
    const detailTitle = document.getElementById('detail-title');
    const detailCopy = document.getElementById('detail-copy');
    const targetKills = document.getElementById('target-kills');
    const raceMargin = document.getElementById('race-margin');
    const control = document.getElementById('control');
    const remaining = document.getElementById('remaining');
    const alliesKills = document.getElementById('allies-kills');
    const axisKills = document.getElementById('axis-kills');
    const alliesBar = document.getElementById('allies-bar');
    const axisBar = document.getElementById('axis-bar');
    const servers = document.getElementById('servers');
    const footerNote = document.getElementById('footer-note');
    const heroServerCount = document.getElementById('hero-server-count');
    const heroServerNames = document.getElementById('hero-server-names');
    const heroLiveCount = document.getElementById('hero-live-count');
    const mobileActivityFeed = document.getElementById('mobile-activity-feed');
    const mobileActivityList = document.getElementById('mobile-activity-list');
    const boardAllTimeObjectiveTotal = document.getElementById('board-all-time-objective-total');
    const boardAllTimeObjectiveCopy = document.getElementById('board-all-time-objective-copy');
    const boardAllTimeObjectiveBar = document.getElementById('board-all-time-objective-bar');
    const boardAllTimeObjectiveProgress = document.getElementById('board-all-time-objective-progress');
    const boardAllTimeObjectiveRemaining = document.getElementById('board-all-time-objective-remaining');
    const controlBarFill = document.getElementById('control-bar-fill');
    const controlValueLabel = document.getElementById('control-value-label');
    const zoomInButton = document.getElementById('zoom-in');
    const zoomOutButton = document.getElementById('zoom-out');
    const zoomResetButton = document.getElementById('zoom-reset');
    const focusActiveButton = document.getElementById('focus-active');

    const MAP_ACTIVITY_HISTORY_KEY = 'hll-frontlines-activity-history';
    const MAP_REFRESH_INTERVAL_MS = 30000;
    const MAP_ACTIVITY_WINDOW_MS = 24 * 60 * 60 * 1000;
    const MAP_HISTORY_SNAPSHOT_INTERVAL_MS = 15 * 60 * 1000;

    let currentMaps = [];
    let selectedMapName = null;
    let currentTargetKills = 500;
    let currentView = 'home';
    let initialCameraPosition = null;
    let initialControlTarget = null;
    let modelLoaded = false;
    let modelBounds = null;
    let intelPanelTouchStartX = 0;
    let intelPanelTouchCurrentX = 0;
    let intelPanelSwipeActive = false;
    let mapActivityHistory = loadStoredMapHistory();
    const overlayMarkers = new Map();
    const projectionVector = new THREE.Vector3();

    const FIXED_POLAR_ANGLE = 0.38;
    const FIXED_AZIMUTH_ANGLE = -0.18;

    const renderer = new THREE.WebGLRenderer({ canvas: mapCanvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.98;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x000000);
    scene.fog = null;

    const pmremGenerator = new THREE.PMREMGenerator(renderer);
    const environmentTexture = pmremGenerator.fromScene(new RoomEnvironment(), 0.05).texture;
    scene.environment = environmentTexture;

    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 300);
    const controls = new OrbitControls(camera, mapCanvas);
    controls.enableDamping = false;
    controls.enableRotate = false;
    controls.enablePan = true;
    controls.screenSpacePanning = false;
    controls.zoomToCursor = false;
    controls.minPolarAngle = FIXED_POLAR_ANGLE;
    controls.maxPolarAngle = FIXED_POLAR_ANGLE;
    controls.minAzimuthAngle = FIXED_AZIMUTH_ANGLE;
    controls.maxAzimuthAngle = FIXED_AZIMUTH_ANGLE;
    controls.zoomSpeed = 1.05;
    controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
    controls.mouseButtons.MIDDLE = THREE.MOUSE.DOLLY;
    controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
    controls.touches.ONE = THREE.TOUCH.PAN;
    controls.touches.TWO = THREE.TOUCH.DOLLY_PAN;

    scene.add(new THREE.HemisphereLight(0xf1e4ca, 0x030303, 0.72));

    const sunLight = new THREE.DirectionalLight(0xffefcb, 1.65);
    sunLight.position.set(13, 17, -10);
    scene.add(sunLight);

    const fillLight = new THREE.DirectionalLight(0x6f87a1, 0.28);
    fillLight.position.set(-10, 9, 12);
    scene.add(fillLight);

    const loader = new GLTFLoader();
    const mapRoot = new THREE.Group();
    scene.add(mapRoot);

    function requestRender() {
      if (!battleMapPage.classList.contains('hidden')) {
        renderFrame();
      }
    }

    function isMobileViewport() {
      return window.innerWidth <= 820;
    }

    function setIntelPanelOpen(isOpen) {
      if (!isMobileViewport()) {
        intelPanel.classList.remove('mobile-open');
        intelPanel.classList.remove('swiping');
        intelPanel.style.transform = '';
        intelPanelBackdrop.style.opacity = '';
        intelPanelBackdrop.hidden = true;
        intelPanelBackdrop.classList.remove('visible');
        return;
      }

      intelPanel.classList.remove('swiping');
      intelPanel.style.transform = '';
      intelPanelBackdrop.style.opacity = '';
      intelPanel.classList.toggle('mobile-open', isOpen);
      intelPanelBackdrop.hidden = !isOpen;
      intelPanelBackdrop.classList.toggle('visible', isOpen);
    }

    function setMobileNavOpen(isOpen) {
      mobileNavDrawer.classList.toggle('open', isOpen);
      mobileNavDrawer.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
      mobileNavBackdrop.hidden = !isOpen;
      mobileNavBackdrop.classList.toggle('visible', isOpen);
    }

    function syncNavigationState() {
      const isHomeView = currentView === 'home';
      const isAboutView = currentView === 'about';
      const isOperationsView = currentView === 'operations';
      const isBattleMapOpen = currentView === 'map';

      desktopHomeButton.classList.toggle('active', isHomeView);
      desktopAboutButton.classList.toggle('active', isAboutView);
      desktopOperationsButton.classList.toggle('active', isOperationsView);
      openBattleMapButton.classList.toggle('active', isBattleMapOpen);
      desktopAboutHomeButton.classList.toggle('active', isHomeView);
      desktopAboutPageButton.classList.toggle('active', isAboutView);
      desktopAboutOperationsButton.classList.toggle('active', isOperationsView);
      desktopAboutMapButton.classList.toggle('active', isBattleMapOpen);
      desktopOperationsHomeButton.classList.toggle('active', isHomeView);
      desktopOperationsAboutButton.classList.toggle('active', isAboutView);
      desktopOperationsPageButton.classList.toggle('active', isOperationsView);
      desktopOperationsMapButton.classList.toggle('active', isBattleMapOpen);
      desktopMapHomeButton.classList.toggle('active', isHomeView);
      desktopMapAboutButton.classList.toggle('active', isAboutView);
      desktopMapOperationsButton.classList.toggle('active', isOperationsView);
      desktopMapOpenButton.classList.toggle('active', isBattleMapOpen);
      mobileNavHomeButton.classList.toggle('active', isHomeView);
      mobileNavAboutButton.classList.toggle('active', isAboutView);
      mobileNavOperationsButton.classList.toggle('active', isOperationsView);
      mobileNavBattleMapButton.classList.toggle('active', isBattleMapOpen);
    }

    function handleIntelPanelTouchStart(event) {
      if (!isMobileViewport() || !intelPanel.classList.contains('mobile-open')) {
        return;
      }

      const touch = event.touches[0];
      intelPanelTouchStartX = touch.clientX;
      intelPanelTouchCurrentX = touch.clientX;
      intelPanelSwipeActive = false;
    }

    function handleIntelPanelTouchMove(event) {
      if (!isMobileViewport() || !intelPanel.classList.contains('mobile-open') || !event.touches.length) {
        return;
      }

      const touch = event.touches[0];
      const deltaX = touch.clientX - intelPanelTouchStartX;
      if (deltaX <= 0) {
        return;
      }

      intelPanelTouchCurrentX = touch.clientX;
      intelPanelSwipeActive = true;
      intelPanel.classList.add('swiping');
      intelPanel.style.transform = `translateX(${deltaX}px)`;

      const openness = Math.max(0, Math.min(1, 1 - (deltaX / intelPanel.offsetWidth)));
      intelPanelBackdrop.hidden = false;
      intelPanelBackdrop.classList.add('visible');
      intelPanelBackdrop.style.opacity = String(openness);
      event.preventDefault();
    }

    function handleIntelPanelTouchEnd() {
      if (!isMobileViewport() || !intelPanel.classList.contains('mobile-open')) {
        intelPanelBackdrop.style.opacity = '';
        return;
      }

      const deltaX = Math.max(0, intelPanelTouchCurrentX - intelPanelTouchStartX);
      intelPanel.classList.remove('swiping');
      intelPanel.style.transform = '';
      intelPanelBackdrop.style.opacity = '';

      if (intelPanelSwipeActive && deltaX > Math.min(intelPanel.offsetWidth * 0.28, 120)) {
        setIntelPanelOpen(false);
      } else {
        setIntelPanelOpen(true);
      }

      intelPanelSwipeActive = false;
      intelPanelTouchStartX = 0;
      intelPanelTouchCurrentX = 0;
    }

    function formatFaction(value) {
      return (value || 'neutral').replace(/^./, (char) => char.toUpperCase());
    }

    function percent(value, target) {
      if (!target) return 0;
      return Math.max(0, Math.min(100, Math.round((value / target) * 100)));
    }

    function formatCount(value) {
      return new Intl.NumberFormat('en-GB').format(value || 0);
    }

    function loadStoredMapHistory() {
      try {
        const raw = localStorage.getItem(MAP_ACTIVITY_HISTORY_KEY);
        if (!raw) {
          return [];
        }
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    }

    function buildActivitySnapshot(maps) {
      const snapshots = {};
      (maps || []).forEach((map) => {
        snapshots[map.map_name] = {
          total_kills: Number(map.total_kills || 0),
          control_value: Number(map?.liberation?.control_value || 0),
        };
      });
      return snapshots;
    }

    function pruneActivityHistory(records, now) {
      return (records || []).filter((entry) => now - Number(entry.captured_at || 0) <= MAP_ACTIVITY_WINDOW_MS);
    }

    function storeMapSnapshots(maps) {
      const now = Date.now();
      const nextEntry = {
        captured_at: now,
        maps: buildActivitySnapshot(maps),
      };
      const prunedHistory = pruneActivityHistory(mapActivityHistory, now);
      const latestEntry = prunedHistory[prunedHistory.length - 1];
      if (!latestEntry || now - Number(latestEntry.captured_at || 0) >= MAP_HISTORY_SNAPSHOT_INTERVAL_MS) {
        prunedHistory.push(nextEntry);
      } else {
        prunedHistory[prunedHistory.length - 1] = nextEntry;
      }
      mapActivityHistory = prunedHistory;
      try {
        localStorage.setItem(MAP_ACTIVITY_HISTORY_KEY, JSON.stringify(prunedHistory));
      } catch {
      }
    }

    function withRecentActivityDeltas(maps) {
      const now = Date.now();
      mapActivityHistory = pruneActivityHistory(mapActivityHistory, now);
      return (maps || []).map((map) => {
        const snapshot = mapActivityHistory.find((entry) => entry?.maps?.[map.map_name])?.maps?.[map.map_name];
        const currentControl = Number(map?.liberation?.control_value || 0);
        const currentKills = Number(map.total_kills || 0);
        const deltaControl = snapshot ? currentControl - Number(snapshot.control_value || 0) : 0;
        const deltaKills = snapshot ? currentKills - Number(snapshot.total_kills || 0) : 0;
        const hasDelta = snapshot ? Math.abs(deltaControl) >= 0.05 || deltaKills !== 0 : false;
        return {
          ...map,
          recent_activity: {
            delta_control: Number(deltaControl.toFixed(2)),
            delta_kills: deltaKills,
            has_delta: hasDelta,
          },
        };
      });
    }

    function formatRelativeTime(value) {
      if (!value) {
        return 'No recent update';
      }

      const timestamp = new Date(value).getTime();
      if (Number.isNaN(timestamp)) {
        return 'Recent';
      }

      const deltaSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
      if (deltaSeconds < 60) {
        return `${deltaSeconds}s ago`;
      }

      const deltaMinutes = Math.round(deltaSeconds / 60);
      if (deltaMinutes < 60) {
        return `${deltaMinutes}m ago`;
      }

      const deltaHours = Math.round(deltaMinutes / 60);
      if (deltaHours < 24) {
        return `${deltaHours}h ago`;
      }

      const deltaDays = Math.round(deltaHours / 24);
      return `${deltaDays}d ago`;
    }

    function formatControlMetric(controlValue) {
      return `${Math.abs(Number(controlValue || 0)).toFixed(1)}%`;
    }

    function formatStateLabel(value) {
      return String(value || 'idle')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (char) => char.toUpperCase());
    }

    function describeRecentActivity(map) {
      const deltaControl = Number(map?.recent_activity?.delta_control || 0);
      const hasDelta = Boolean(map?.recent_activity?.has_delta);

      if (hasDelta && deltaControl > 0) {
        return {
          copy: `Allies pushed +${Math.abs(deltaControl).toFixed(2)}%`,
          metric: `+${Math.abs(deltaControl).toFixed(2)}%`,
          tone: 'allies',
        };
      }

      if (hasDelta && deltaControl < 0) {
        return {
          copy: `Axis pushed +${Math.abs(deltaControl).toFixed(2)}%`,
          metric: `+${Math.abs(deltaControl).toFixed(2)}%`,
          tone: 'axis',
        };
      }

      return {
        copy: '',
        metric: '0.00%',
        tone: '',
      };
    }

    function renderMobileActivityFeed(maps) {
      if (!mobileActivityFeed || !mobileActivityList) {
        return;
      }

      const items = [...(maps || [])]
        .sort((left, right) => {
          const leftDelta = Math.abs(Number(left?.recent_activity?.delta_control || 0));
          const rightDelta = Math.abs(Number(right?.recent_activity?.delta_control || 0));
          if (leftDelta !== rightDelta) {
            return rightDelta - leftDelta;
          }
          const leftKills = Math.abs(Number(left?.recent_activity?.delta_kills || 0));
          const rightKills = Math.abs(Number(right?.recent_activity?.delta_kills || 0));
          if (leftKills !== rightKills) {
            return rightKills - leftKills;
          }
          return new Date(right.updated_at || 0).getTime() - new Date(left.updated_at || 0).getTime();
        })
        .slice(0, 2);

      mobileActivityList.innerHTML = '';
      mobileActivityFeed.hidden = items.length === 0;
      items.forEach((map) => {
        const activity = describeRecentActivity(map);
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'mobile-activity-item';
        item.innerHTML = `
          <div class="mobile-activity-time">${formatRelativeTime(map.updated_at)}</div>
          <div>
            <div class="mobile-activity-name">${map.map_name}</div>
            ${activity.copy ? `<div class="mobile-activity-copy">${activity.copy}</div>` : ''}
          </div>
          <div class="mobile-activity-impact ${activity.tone}">${activity.metric}</div>
        `;
        item.addEventListener('click', () => fetchMapDetail(map.map_name));
        mobileActivityList.appendChild(item);
      });
    }

    function controlBarConfig(controlValue) {
      const safeValue = Math.max(-100, Math.min(100, Number(controlValue || 0)));
      const width = `${Math.abs(safeValue) / 2}%`;
      if (safeValue >= 0) {
        return {
          className: 'allies',
          left: '50%',
          width,
        };
      }
      return {
        className: 'axis',
        left: `${50 - (Math.abs(safeValue) / 2)}%`,
        width,
      };
    }

    function buildMiniControlTrack(controlValue) {
      const config = controlBarConfig(controlValue);
      return `<div class="mini-control-track"><span class="mini-control-fill ${config.className}" style="left: ${config.left}; width: ${config.width}"></span></div>`;
    }

    function renderAllTimeObjective(objective) {
      const targetKills = objective?.target_kills || 25000000;
      const totalKills = objective?.total_kills || 0;
      const remainingKills = objective?.remaining_kills ?? Math.max(targetKills - totalKills, 0);
      const progressPercent = objective?.progress_percent || 0;

      boardAllTimeObjectiveTotal.textContent = `${formatCount(totalKills)} / ${formatCount(targetKills)}`;
      boardAllTimeObjectiveCopy.textContent = '';
      boardAllTimeObjectiveBar.style.width = `${progressPercent}%`;
      boardAllTimeObjectiveProgress.textContent = `${progressPercent}% complete`;
      boardAllTimeObjectiveRemaining.textContent = `${formatCount(remainingKills)} remaining`;
    }

    function mergeMapPayloadIntoState(payload) {
      const mapName = payload?.map_name;
      if (!mapName) {
        return;
      }

      const nextEntry = {
        map_name: mapName,
        map_id: payload.map_id,
        allied_kills: payload.allied_kills || 0,
        axis_kills: payload.axis_kills || 0,
        total_kills: payload.total_kills || 0,
        updated_at: payload.updated_at,
        servers: payload.servers || [],
        is_active_battle: Boolean(payload.is_active_battle),
        active_servers: payload.active_servers || [],
        liberation: payload.liberation || {},
      };

      const existingIndex = currentMaps.findIndex((item) => item.map_name === mapName);
      if (existingIndex >= 0) {
        currentMaps[existingIndex] = { ...currentMaps[existingIndex], ...nextEntry };
        return;
      }

      currentMaps.push(nextEntry);
    }

    function formatActiveServers(activeServers) {
      return (activeServers || [])
        .map((server) => server.server_name || server.server_id)
        .filter(Boolean)
        .join(', ');
    }

    function getFrontRegion(mapName) {
      return FRONT_LAYOUT[mapName]?.region || 'Reserve Front';
    }

    function getFrontLayout(mapName, index) {
      const known = FRONT_LAYOUT[mapName];
      if (known) {
        return known;
      }

      const columns = 4;
      return {
        region: 'Reserve Front',
        x: 860 + (index % columns) * 220,
        y: 1010 + Math.floor(index / columns) * 170,
      };
    }

    function setActiveFront(mapName) {
      document.querySelectorAll('.front-chip').forEach((chip) => {
        chip.classList.toggle('active', chip.dataset.mapName === mapName);
      });

      overlayMarkers.forEach((marker, name) => {
        marker.classList.toggle('active', name === mapName);
      });
    }

    function buildOverlayMarker(map, index, target) {
      const lib = map.liberation || {};
      const activeServers = formatActiveServers(map.active_servers);
      const layout = getFrontLayout(map.map_name, index);
      const controlLane = buildMiniControlTrack(lib.control_value || 0);
      const marker = document.createElement('button');
      marker.type = 'button';
      marker.className = `map-marker ${map.is_active_battle ? 'live' : ''}`;
      marker.dataset.mapName = map.map_name;
      marker.innerHTML = `
        <div class="map-marker-top">
          <div>
            <div class="map-marker-region">${layout.region}</div>
            <div class="map-marker-name">${map.map_name}</div>
          </div>
          <span class="map-marker-badge ${map.is_active_battle ? 'live' : ''}">${map.is_active_battle ? `ACTIVE - ${activeServers || 'tracked server'}` : formatStateLabel(lib.state)}</span>
        </div>
        <div class="map-marker-lanes">
          ${controlLane}
        </div>
        <div class="map-marker-meta"><span class="map-marker-state">${formatStateLabel(lib.state)}</span><span>${lib.control_value || 0}% | ${formatCount(map.total_kills || 0)} kills</span></div>
      `;
      marker.addEventListener('click', () => fetchMapDetail(map.map_name));
      return marker;
    }

    function ensureOverlayMarkers(maps, target) {
      const nextNames = new Set(maps.map((map) => map.map_name));

      overlayMarkers.forEach((marker, name) => {
        if (!nextNames.has(name)) {
          marker.remove();
          overlayMarkers.delete(name);
        }
      });

      maps.forEach((map, index) => {
        const marker = buildOverlayMarker(map, index, target);
        const existing = overlayMarkers.get(map.map_name);
        if (existing) {
          existing.replaceWith(marker);
        } else {
          mapOverlayLayer.appendChild(marker);
        }
        overlayMarkers.set(map.map_name, marker);
      });
    }

    function mapToWorldPosition(mapName, index) {
      if (!modelBounds) {
        return null;
      }

      const layout = getFrontLayout(mapName, index);
      const normalizedX = layout.x / MAP_LAYOUT_WIDTH;
      const normalizedY = layout.y / MAP_LAYOUT_HEIGHT;
      const x = THREE.MathUtils.lerp(modelBounds.min.x, modelBounds.max.x, normalizedX);
      const z = THREE.MathUtils.lerp(modelBounds.min.z, modelBounds.max.z, normalizedY);
      const y = modelBounds.max.y + Math.max(modelBounds.size.y * 0.12, 0.18);
      return new THREE.Vector3(x, y, z);
    }

    function updateOverlayPositions() {
      if (!modelLoaded || !modelBounds) {
        return;
      }

      const rect = mapViewport.getBoundingClientRect();
      currentMaps.forEach((map, index) => {
        const marker = overlayMarkers.get(map.map_name);
        if (!marker) {
          return;
        }

        const worldPosition = mapToWorldPosition(map.map_name, index);
        if (!worldPosition) {
          marker.classList.add('hidden');
          return;
        }

        projectionVector.copy(worldPosition).project(camera);
        const inView = projectionVector.z > -1 && projectionVector.z < 1;
        const x = (projectionVector.x * 0.5 + 0.5) * rect.width;
        const y = (-projectionVector.y * 0.5 + 0.5) * rect.height;
        const withinBounds = x >= -180 && x <= rect.width + 180 && y >= -100 && y <= rect.height + 100;

        if (!inView || !withinBounds) {
          marker.classList.add('hidden');
          return;
        }

        marker.classList.remove('hidden');
        marker.style.left = `${x}px`;
        marker.style.top = `${y}px`;
      });
    }

    function renderChallengeTracks(tracks) {
      challengeGrid.innerHTML = '';
      const items = [tracks?.current, tracks?.weekly, tracks?.monthly].filter(Boolean);
      if (!items.length) {
        challengeSummary.textContent = '';
        return;
      }

      challengeSummary.textContent = '';

      items.forEach((track) => {
        const card = document.createElement('article');
        card.className = `challenge-card ${track.status === 'active' ? 'active' : ''}`;
        card.innerHTML = `
          <span class="challenge-label">${track.window || 'reserve'}</span>
          <h3 class="challenge-title">${track.label}</h3>
          <p class="challenge-copy">${track.description || 'Challenge slot reserved.'}</p>
          <span class="challenge-tag">${track.status || 'planned'}</span>
        `;
        challengeGrid.appendChild(card);
      });
    }

    function renderDetail(payload, options = {}) {
      const { openPanel = false } = options;
      const lib = payload.liberation || {};
      const allies = payload.allied_kills || 0;
      const axis = payload.axis_kills || 0;
      const total = payload.total_kills || (allies + axis);
      const activeBattle = Boolean(payload.is_active_battle);
      const activeServerNames = formatActiveServers(payload.active_servers);
      const controlConfig = controlBarConfig(lib.control_value || 0);

      statusPill.textContent = activeBattle
        ? `active - ${activeServerNames || 'tracked server'}`
        : formatStateLabel(lib.state || 'idle');
      detailTitle.textContent = payload.map_name || 'Unknown front';
      detailCopy.textContent = activeBattle
        ? `${activeServerNames || 'Tracked server'} is active on this front.`
        : '';
      targetKills.textContent = `${lib.control_target || 100}%`;
      raceMargin.textContent = `${lib.control_value || 0}%`;
      control.textContent = formatFaction(lib.occupied_faction || lib.controlling_faction);
      remaining.textContent = formatStateLabel(lib.state || 'idle');
      controlValueLabel.textContent = `${lib.control_value || 0}%`;
      controlBarFill.className = `control-fill ${controlConfig.className}`;
      controlBarFill.style.left = controlConfig.left;
      controlBarFill.style.width = controlConfig.width;
      alliesKills.textContent = formatCount(allies);
      axisKills.textContent = formatCount(axis);
      alliesBar.style.width = `${total > 0 ? (allies / total) * 100 : 0}%`;
      axisBar.style.width = `${total > 0 ? (axis / total) * 100 : 0}%`;

      servers.innerHTML = '';
      (payload.servers || []).forEach((server) => {
        const row = document.createElement('div');
        row.className = 'server-item';
        row.innerHTML = `
          <div class="server-line"><strong>${server.server_id}</strong><span class="server-metric">${formatCount(server.allied_kills + server.axis_kills)} total</span></div>
          <div class="server-line"><span>Allies ${formatCount(server.allied_kills)}</span><span>Axis ${formatCount(server.axis_kills)}</span></div>
        `;
        servers.appendChild(row);
      });

      footerNote.textContent = payload.updated_at ? `Last updated ${payload.updated_at}` : 'No update timestamp available';
      setActiveFront(payload.map_name || '');
      if (openPanel) {
        setIntelPanelOpen(true);
      }
    }

    async function fetchMapDetail(mapName) {
      selectedMapName = mapName;
      const response = await fetch(`/api/maps/${encodeURIComponent(mapName)}`);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      mergeMapPayloadIntoState(payload);
      renderFrontStrip(currentMaps, currentTargetKills);
      renderDetail(payload, { openPanel: true });
    }

    function renderFrontStrip(maps, target) {
      currentTargetKills = target || currentTargetKills;
      frontStrip.innerHTML = '';
      const trackedServers = new Map();
      maps.forEach((map) => {
        (map.servers || []).forEach((server) => {
          const key = server.server_id || server.server_name;
          if (!key) {
            return;
          }
          trackedServers.set(key, server.server_name || server.server_id);
        });

        (map.active_servers || []).forEach((server) => {
          const key = server.server_id || server.server_name;
          if (!key) {
            return;
          }
          trackedServers.set(key, server.server_name || server.server_id);
        });
      });

      const trackedServerNames = [...trackedServers.values()];
      heroServerCount.textContent = trackedServerNames.length || 1;
  heroServerNames.textContent = trackedServerNames.join(' | ') || '7DR';
      heroLiveCount.textContent = maps.filter((map) => map.is_active_battle).length;
      renderMobileActivityFeed(maps);
      emptyState.hidden = maps.length > 0;

      maps.forEach((map, index) => {
        const lib = map.liberation || {};
        const layout = getFrontLayout(map.map_name, index);
        const activeServers = formatActiveServers(map.active_servers);
        const controlLane = buildMiniControlTrack(lib.control_value || 0);
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = `front-chip ${map.is_active_battle ? 'live' : ''}`;
        chip.dataset.mapName = map.map_name;
        chip.innerHTML = `
          <div class="front-chip-top">
            <div>
              <div class="front-chip-name">${map.map_name}</div>
            </div>
            <span class="front-chip-badge ${map.is_active_battle ? 'live' : ''}">${map.is_active_battle ? `ACTIVE - ${activeServers || 'tracked server'}` : formatStateLabel(lib.state)}</span>
          </div>
          <div class="front-chip-lanes">
            ${controlLane}
          </div>
          <div class="front-chip-meta"><span class="front-chip-state">${formatStateLabel(lib.state)}</span><span>${lib.control_value || 0}% | ${formatCount(map.total_kills || 0)} kills</span></div>
        `;
        chip.addEventListener('click', () => fetchMapDetail(map.map_name));
        frontStrip.appendChild(chip);

        if (selectedMapName && selectedMapName === map.map_name) {
          renderDetail(map);
        } else if (!selectedMapName && index === 0) {
          selectedMapName = map.map_name;
          renderDetail(map);
        }
      });

      if (selectedMapName && !maps.some((map) => map.map_name === selectedMapName) && maps[0]) {
        selectedMapName = maps[0].map_name;
        renderDetail(maps[0]);
      }

      ensureOverlayMarkers(maps, target);
      setActiveFront(selectedMapName || '');
    }

    function resizeRenderer() {
      const rect = mapViewport.getBoundingClientRect();
      if (!rect.width || !rect.height) {
        return;
      }
      renderer.setSize(rect.width, rect.height, false);
      camera.aspect = rect.width / rect.height;
      camera.updateProjectionMatrix();
    }

    function renderFrame() {
      renderer.render(scene, camera);
      updateOverlayPositions();
    }

    function fitCameraToModel(root) {
      const originalBox = new THREE.Box3().setFromObject(root);
      const originalSize = originalBox.getSize(new THREE.Vector3());
      const originalCenter = originalBox.getCenter(new THREE.Vector3());
      const originalMaxDim = Math.max(originalSize.x, originalSize.y, originalSize.z) || 10;
      const desiredSize = 26;
      const scaleFactor = desiredSize / originalMaxDim;

      root.scale.setScalar(scaleFactor);
      root.position.copy(originalCenter).multiplyScalar(-scaleFactor);

      const box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z) || desiredSize;
      modelBounds = {
        min: box.min.clone(),
        max: box.max.clone(),
        size: size.clone(),
        center: center.clone(),
      };

      controls.target.set(center.x, center.y, center.z);
      camera.near = Math.max(maxDim / 200, 0.05);
      camera.far = maxDim * 12;
      camera.fov = 26;
      camera.position.set(center.x + maxDim * 0.12, center.y + maxDim * 1.24, center.z - maxDim * 0.08);
      camera.lookAt(controls.target);
      controls.minDistance = maxDim * 0.18;
      controls.maxDistance = maxDim * 2.2;
      controls.panSpeed = Math.max(maxDim / 20, 0.85);
      scene.fog = null;
      camera.updateProjectionMatrix();
      controls.update();
      initialCameraPosition = camera.position.clone();
      initialControlTarget = controls.target.clone();
      renderFrame();
    }

    function zoomCamera(multiplier) {
      const offset = camera.position.clone().sub(controls.target);
      const nextDistance = THREE.MathUtils.clamp(offset.length() * multiplier, controls.minDistance, controls.maxDistance);
      offset.setLength(nextDistance);
      camera.position.copy(controls.target).add(offset);
      requestRender();
    }

    async function loadModel() {
      try {
        const gltf = await loader.loadAsync('/europe_with_4k_heightmap.glb');
        gltf.scene.traverse((child) => {
          if (child.isMesh) {
            child.castShadow = false;
            child.receiveShadow = false;
            if (child.material) {
              child.material.envMapIntensity = 0.42;
              if ('roughness' in child.material) {
                child.material.roughness = Math.min(child.material.roughness ?? 1, 0.88);
              }
              if ('metalness' in child.material) {
                child.material.metalness = Math.max(child.material.metalness ?? 0, 0);
              }
              if (child.material.map) {
                child.material.map.anisotropy = Math.min(renderer.capabilities.getMaxAnisotropy(), 4);
                child.material.map.colorSpace = THREE.SRGBColorSpace;
              }
              if (child.material.normalMap) {
                child.material.normalMap.anisotropy = Math.min(renderer.capabilities.getMaxAnisotropy(), 4);
              }
              if (child.material.aoMap) {
                child.material.aoMap.anisotropy = Math.min(renderer.capabilities.getMaxAnisotropy(), 4);
              }
              child.material.needsUpdate = true;
            }
          }
        });
        mapRoot.clear();
        mapRoot.add(gltf.scene);
        fitCameraToModel(gltf.scene);
        modelLoaded = true;
        mapLoading.classList.add('hidden');
        updateOverlayPositions();
      } catch (error) {
        console.error(error);
        mapLoading.textContent = 'Failed to load europe_with_4k_heightmap.glb';
        footerNote.textContent = `Model load failed: ${error.message}`;
      }
    }

    async function loadMaps() {
      const response = await fetch('/api/maps');
      const payload = await response.json();
      const maps = withRecentActivityDeltas(payload.maps || []);
      const target = payload.target_kills || 500;
      currentMaps = maps;
      currentTargetKills = target;
      storeMapSnapshots(maps);

      renderAllTimeObjective(payload.all_time_objective);
      renderChallengeTracks(payload.challenge_tracks);
      renderFrontStrip(maps, target);

      if (maps.length) {
        footerNote.textContent = `${maps.length} fronts loaded with live campaign state.`;
      } else {
        footerNote.textContent = 'No campaign fronts tracked yet.';
      }
    }

    function resetCamera() {
      if (!initialCameraPosition || !initialControlTarget) {
        return;
      }
      camera.position.copy(initialCameraPosition);
      controls.target.copy(initialControlTarget);
      requestRender();
    }

    function setView(view) {
      currentView = view;
      const isHomeView = view === 'home';
      const isAboutView = view === 'about';
      const isOperationsView = view === 'operations';
      const isBattleMapOpen = view === 'map';

      dashboardView.classList.toggle('hidden', !isHomeView);
      aboutView.classList.toggle('hidden', !isAboutView);
      operationsView.classList.toggle('hidden', !isOperationsView);
      battleMapPage.classList.toggle('hidden', !isBattleMapOpen);
      pageRoot.classList.toggle('about-mode', isAboutView || isOperationsView);
      intelPanel.classList.toggle('hidden', isAboutView || isOperationsView);
      syncNavigationState();
      setMobileNavOpen(false);
      if (!isHomeView) {
        setIntelPanelOpen(false);
      }
      if (isBattleMapOpen) {
        resizeRenderer();
        requestRender();
      }
    }

    function setBattleMapOpen(isOpen) {
      setView(isOpen ? 'map' : 'home');
    }

    function moveCameraToWorldPosition(worldPosition) {
      if (!worldPosition) {
        return;
      }

      const offset = camera.position.clone().sub(controls.target);
      controls.target.copy(worldPosition);
      camera.position.copy(worldPosition).add(offset);
      requestRender();
    }

    function focusActiveFront() {
      const activeMap = currentMaps.find((map) => map.is_active_battle) || currentMaps[0];
      if (!activeMap) {
        return;
      }

      const activeIndex = currentMaps.findIndex((map) => map.map_name === activeMap.map_name);
      const worldPosition = mapToWorldPosition(activeMap.map_name, activeIndex >= 0 ? activeIndex : 0);
      fetchMapDetail(activeMap.map_name).catch((error) => {
        footerNote.textContent = `Active front focus failed: ${error.message}`;
      });
      moveCameraToWorldPosition(worldPosition);
    }

    function initializeInteractions() {
      resizeRenderer();

      mapCanvas.addEventListener('contextmenu', (event) => {
        event.preventDefault();
      });

      controls.addEventListener('change', requestRender);

      zoomInButton.addEventListener('click', () => zoomCamera(0.84));
      zoomOutButton.addEventListener('click', () => zoomCamera(1.18));
      zoomResetButton.addEventListener('click', resetCamera);
      focusActiveButton.addEventListener('click', focusActiveFront);
      desktopHomeButton.addEventListener('click', () => setView('home'));
      desktopAboutButton.addEventListener('click', () => setView('about'));
      desktopOperationsButton.addEventListener('click', () => setView('operations'));
      openBattleMapButton.addEventListener('click', () => setView('map'));
      desktopAboutHomeButton.addEventListener('click', () => setView('home'));
      desktopAboutPageButton.addEventListener('click', () => setView('about'));
      desktopAboutOperationsButton.addEventListener('click', () => setView('operations'));
      desktopAboutMapButton.addEventListener('click', () => setView('map'));
      desktopOperationsHomeButton.addEventListener('click', () => setView('home'));
      desktopOperationsAboutButton.addEventListener('click', () => setView('about'));
      desktopOperationsPageButton.addEventListener('click', () => setView('operations'));
      desktopOperationsMapButton.addEventListener('click', () => setView('map'));
      desktopMapHomeButton.addEventListener('click', () => setView('home'));
      desktopMapAboutButton.addEventListener('click', () => setView('about'));
      desktopMapOperationsButton.addEventListener('click', () => setView('operations'));
      desktopMapOpenButton.addEventListener('click', () => setView('map'));
      returnDashboardButton.addEventListener('click', () => setView('home'));
      mobileNavToggleButtons.forEach((button) => button.addEventListener('click', () => setMobileNavOpen(true)));
      mobileNavCloseButton.addEventListener('click', () => setMobileNavOpen(false));
      mobileNavBackdrop.addEventListener('click', () => setMobileNavOpen(false));
      mobileNavHomeButton.addEventListener('click', () => setView('home'));
      mobileNavAboutButton.addEventListener('click', () => setView('about'));
      mobileNavOperationsButton.addEventListener('click', () => setView('operations'));
      mobileNavBattleMapButton.addEventListener('click', () => setView('map'));
      intelPanelCloseButton.addEventListener('click', () => setIntelPanelOpen(false));
      intelPanelBackdrop.addEventListener('click', () => setIntelPanelOpen(false));
      intelPanel.addEventListener('touchstart', handleIntelPanelTouchStart, { passive: true });
      intelPanel.addEventListener('touchmove', handleIntelPanelTouchMove, { passive: false });
      intelPanel.addEventListener('touchend', handleIntelPanelTouchEnd);
      intelPanel.addEventListener('touchcancel', handleIntelPanelTouchEnd);

      window.addEventListener('resize', () => {
        resizeRenderer();
        requestRender();
        if (!isMobileViewport()) {
          setIntelPanelOpen(false);
          setMobileNavOpen(false);
        } else {
          intelPanelBackdrop.style.opacity = '';
        }
      });
    }

    initializeInteractions();
    await loadModel();
    setView('home');
    loadMaps().catch((error) => {
      footerNote.textContent = `Failed to load campaign data: ${error.message}`;
    });
    window.setInterval(() => {
      loadMaps().catch((error) => {
        footerNote.textContent = `Failed to refresh campaign data: ${error.message}`;
      });
    }, MAP_REFRESH_INTERVAL_MS);
