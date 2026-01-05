from caal.state_tracker import StateTracker, GuardState


def test_double_confirm_flow():
    st = StateTracker()
    decision = {"command_id": "cmd.x", "authority_level": 4}
    # first confirm requested
    msg = st.start_pending(decision, chain_id="chain1")
    assert "requires confirmation" in msg
    assert st.pending is not None
    # simulate confirm action
    pending = st.confirm_pending()
    assert pending.command_id == "cmd.x"
    st.enter_state(GuardState.DEFAULT)
    # pending cleared
    assert st.pending is None
