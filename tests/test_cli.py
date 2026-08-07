import pytest

from textris import DEFAULT_SERVER_URL, build_parser


def test_server_defaults_to_a_local_player() -> None:
    args = build_parser().parse_args(["server", "--name", "tin"])

    assert args.command == "server"
    assert args.headless is False
    assert args.name == "tin"
    assert args.host == "0.0.0.0"
    assert args.port == 8765


def test_connect_defaults_to_the_local_server() -> None:
    args = build_parser().parse_args(["connect"])

    assert args.command == "connect"
    assert args.url == DEFAULT_SERVER_URL
    assert args.name is None


def test_headless_server_cannot_have_a_local_player_name() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["server", "--headless", "--name", "tin"])
