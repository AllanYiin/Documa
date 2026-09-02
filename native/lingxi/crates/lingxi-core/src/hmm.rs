//! Legacy module placeholder.
//!
//! LingXi 0.3.0 no longer performs a second OOV re-cut pass. The complete
//! second-order BMES calculation now lives in `dag::cut_viterbi`, where every
//! candidate span competes in one global decoder and dictionary spans replace
//! only their internal BMES score.