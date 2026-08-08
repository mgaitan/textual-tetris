from textris import DEFAULT_SERVER_URL, build_parser


def test_server_defaults_to_a_local_player() -> None:
    args = build_parser().parse_args(["server", "--name", "tin"])

    assert args.command == "server"
    assert args.headless is False
    assert args.name == "tin"
    assert args.host == "0.0.0.0"
    assert args.port == 8765


def test_server_can_watch_without_occupying_a_player_slot() -> None:
    args = build_parser().parse_args(["server", "--watch-only"])

    assert args.command == "server"
    assert args.watch_only is True
    assert args.headless is False
    assert args.name is None


def test_connect_defaults_to_the_local_server() -> None:
    args = build_parser().parse_args(["connect"])

    assert args.command == "connect"
    assert args.url == DEFAULT_SERVER_URL
    assert args.name is None


def test_connect_can_be_watch_only() -> None:
    args = build_parser().parse_args(["connect", "--watch-only", "ws://example.test:8765"])

    assert args.url == "ws://example.test:8765"
    assert args.watch_only is True


def test_headless_server_cannot_have_a_local_player_name() -> None:
    try:
        build_parser().parse_args(["server", "--headless", "--name", "tin"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("Mutually exclusive server options were accepted")


def test_headless_and_watch_only_server_modes_are_exclusive() -> None:
    try:
        build_parser().parse_args(["server", "--headless", "--watch-only"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("Mutually exclusive server modes were accepted")
