/* Native HA thermostat with a device-confirmed child-lock control in the top-left header. */
class BehaThermostatCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; position:relative; height:100%; }
        #dial { height:100%; }
        button { position:absolute; z-index:1; left:4px; top:4px;
          width:40px; height:40px; padding:0; justify-content:center;
          display:flex; align-items:center; gap:6px; cursor:pointer;
          border:1px solid transparent; border-radius:50%;
          background:transparent;
          color:var(--secondary-text-color); font:500 11px var(--paper-font-body1_-_font-family, sans-serif); }
        button:hover:not(:disabled) { background:var(--secondary-background-color); }
        button[aria-checked="true"] { color:var(--primary-color); border-color:var(--primary-color); }
        button:focus-visible { outline:2px solid var(--primary-color); outline-offset:3px; }
        button:disabled { opacity:.45; cursor:default; }
        ha-icon { --mdc-icon-size:20px; }
      </style>
      <div id="dial"></div>
      <button type="button" role="switch" aria-label="Child lock" disabled>
        <ha-icon icon="mdi:lock-open-variant-outline"></ha-icon>
      </button>`;
    this._button = this.shadowRoot.querySelector('button');
    this._button.addEventListener('click', () => this._toggle());
  }
  setConfig(config) {
    if (!config.entity?.startsWith('climate.') || !config.child_lock_entity?.startsWith('switch.')) {
      throw new Error('Specify a climate entity and child_lock_entity switch');
    }
    this._config = config;
    this._build();
  }
  async _build() {
    const helpers = await window.loadCardHelpers();
    if (!this._card) {
      this._card = helpers.createCardElement({ type:'thermostat', entity:this._config.entity });
      this.shadowRoot.getElementById('dial').replaceChildren(this._card);
    }
    this._card.setConfig({ type:'thermostat', entity:this._config.entity,
      name:this._config.name, show_current_as_primary:true,
      features:[{type:'climate-hvac-modes', hvac_modes:['off','heat']}] });
    if (this._hass) this._card.hass = this._hass;
  }
  set hass(hass) {
    this._hass = hass;
    if (this._card) this._card.hass = hass;
    const state = hass.states[this._config?.child_lock_entity]?.state;
    if (this._pending === state) { clearTimeout(this._timer); this._pending = null; }
    this._button.disabled = !['on','off'].includes(state) || Boolean(this._pending);
    this._button.setAttribute('aria-checked', String(state === 'on'));
    this._button.setAttribute('aria-busy', String(Boolean(this._pending)));
    const label = `${this._config?.name || 'Heater'} child lock: ${state || 'unavailable'}`;
    this._button.setAttribute('aria-label',label);
    this._button.title = this._pending ? 'Waiting for heater confirmation' : label;
    this._button.querySelector('ha-icon').setAttribute('icon', state === 'on' ? 'mdi:lock' : 'mdi:lock-open-variant-outline');
  }
  _notify(message) {
    this.dispatchEvent(new CustomEvent('hass-notification', { bubbles:true, composed:true, detail:{ message } }));
  }
  async _toggle() {
    const state = this._hass?.states[this._config.child_lock_entity]?.state;
    if (!['on','off'].includes(state) || this._pending) return;
    this._pending = state === 'on' ? 'off' : 'on';
    this.hass = this._hass;
    this._timer = setTimeout(() => {
      this._pending = null;
      this.hass = this._hass;
      this._notify('Child lock was not confirmed by the heater. Please retry.');
    }, 45000);
    try {
      await this._hass.callService('switch', this._pending === 'on' ? 'turn_on' : 'turn_off',
        { entity_id:this._config.child_lock_entity });
    } catch (error) {
      clearTimeout(this._timer); this._pending = null; this.hass = this._hass;
      this._notify(`Could not change child lock: ${error.message || error}`);
    }
  }
  getCardSize() { return 7; }
  getGridOptions() { return {columns:12, rows:6, min_columns:6, min_rows:4}; }
}
if (!customElements.get('beha-thermostat-card')) customElements.define('beha-thermostat-card',BehaThermostatCard);
