// Product decision: Re-bake distracts from the simple "Create beat" flow at
// this stage of the product. Every Re-bake-related surface (the Tools
// button, its onBake handler, the Tools description mentioning it, and the
// matching FAQ entry) stays fully wired/present in code — this single flag
// just keeps all of it out of the rendered UI. Flip to true to restore
// everything at once; no other changes needed.
export const SHOW_REBAKE = false;
