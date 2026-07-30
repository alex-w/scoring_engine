import json
from datetime import datetime, timedelta

import pytest
import pytz

from scoring_engine.db import db
from scoring_engine.models.flag import Flag, FlagTypeEnum, Perm, Platform, Solve
from scoring_engine.models.team import Team


class TestFlag:

    def test_init_file_flag(self):
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt", "content": "flag{test123}"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        assert flag.type == FlagTypeEnum.file
        assert flag.platform == Platform.nix
        assert flag.data == {"path": "/root/flag.txt", "content": "flag{test123}"}
        assert flag.start_time == start_time
        assert flag.end_time == end_time
        assert flag.perm == Perm.root
        assert flag.dummy is False

    def test_init_pipe_flag(self):
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.pipe,
            platform=Platform.windows,
            data={"name": "flagpipe", "content": "flag{pipe123}"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.user,
            dummy=False,
        )
        assert flag.type == FlagTypeEnum.pipe
        assert flag.platform == Platform.windows
        assert flag.perm == Perm.user

    def test_init_net_flag(self):
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.net,
            platform=Platform.nix,
            data={"port": 8080, "content": "flag{net123}"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        assert flag.type == FlagTypeEnum.net
        assert flag.data["port"] == 8080

    def test_init_reg_flag(self):
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.reg,
            platform=Platform.windows,
            data={"key": "HKEY_LOCAL_MACHINE\\SOFTWARE\\Flag", "value": "flag{reg123}"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        assert flag.type == FlagTypeEnum.reg
        assert flag.platform == Platform.windows

    def test_dummy_flag(self):
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/tmp/dummy.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.user,
            dummy=True,
        )
        assert flag.dummy is True

    def test_simple_save(self):
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()
        assert flag.id is not None
        assert len(db.session.query(Flag).all()) == 1

    def test_as_dict(self):
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt", "content": "flag{test123}"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        flag_dict = flag.as_dict()
        assert "id" in flag_dict
        assert flag_dict["type"] == "file"
        assert flag_dict["platform"] == "nix"
        assert flag_dict["data"] == {"path": "/root/flag.txt", "content": "flag{test123}"}
        assert flag_dict["start_time"] == int(start_time.timestamp())
        assert flag_dict["end_time"] == int(end_time.timestamp())
        assert flag_dict["perm"] == "root"
        assert flag_dict["dummy"] is False

    def test_as_dict_all_enums(self):
        """Test as_dict serialization for all enum combinations"""
        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)

        # Test with different enum combinations
        flag = Flag(
            type=FlagTypeEnum.net,
            platform=Platform.windows,
            data={"port": 9999},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.user,
            dummy=True,
        )
        db.session.add(flag)
        db.session.commit()

        flag_dict = flag.as_dict()
        assert flag_dict["type"] == "net"
        assert flag_dict["platform"] == "win"
        assert flag_dict["perm"] == "user"
        assert flag_dict["dummy"] is True

    def test_localize_start_time(self):
        """Test that start_time is properly localized to configured timezone"""
        start_time = datetime(2025, 1, 1, 12, 0, 0)  # Naive datetime (UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/test"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        localized = flag.localize_start_time
        # Should be a string in format "YYYY-MM-DD HH:MM:SS TZ"
        assert isinstance(localized, str)
        assert "2025-01-01" in localized
        # Should contain timezone abbreviation
        assert any(tz in localized for tz in ["UTC", "EST", "PST", "MST", "CST"])

    def test_localize_end_time(self):
        """Test that end_time is properly localized to configured timezone"""
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        end_time = datetime(2025, 1, 1, 18, 0, 0)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/test"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        localized = flag.localize_end_time
        # Should be a string in format "YYYY-MM-DD HH:MM:SS TZ"
        assert isinstance(localized, str)
        assert "2025-01-01" in localized
        assert any(tz in localized for tz in ["UTC", "EST", "PST", "MST", "CST"])

    def test_data_round_trips_through_the_database(self):
        """data is a JSON column, not pickle: it must survive a real round trip."""
        payload = {
            "path": "/root/flag.txt",
            "content": "flag{éà中文}",
            "port": 8080,
            "nested": {"list": [1, 2, 3], "null": None, "bool": True},
            "escaped": "C:\\Windows\\System32",
        }
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data=payload,
            start_time=datetime(2025, 1, 1, 12, 0, 0),
            end_time=datetime(2025, 1, 1, 18, 0, 0),
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()
        flag_id = flag.id
        db.session.expunge_all()

        reloaded = db.session.get(Flag, flag_id)
        assert reloaded.data == payload
        assert isinstance(reloaded.data, dict)

    def test_data_is_stored_as_json_text_not_pickle(self):
        """Guards against a regression back to PickleType."""
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=datetime(2025, 1, 1, 12, 0, 0),
            end_time=datetime(2025, 1, 1, 18, 0, 0),
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        raw = db.session.execute(db.text("SELECT data FROM flags WHERE id = :id"), {"id": flag.id}).scalar()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        assert json.loads(raw) == {"path": "/root/flag.txt"}

    def test_data_is_queryable_in_sql(self):
        """The whole point of dropping pickle: the column can be queried."""
        for index, path in enumerate(["/root/a.txt", "/root/b.txt"]):
            db.session.add(
                Flag(
                    type=FlagTypeEnum.file,
                    platform=Platform.nix,
                    data={"path": path},
                    start_time=datetime(2025, 1, 1, 12, 0, 0),
                    end_time=datetime(2025, 1, 1, 18, 0, 0),
                    perm=Perm.root,
                    dummy=False,
                )
            )
        db.session.commit()

        rows = db.session.execute(
            db.text("SELECT id FROM flags WHERE json_extract(data, '$.path') = '/root/b.txt'")
        ).fetchall()
        assert len(rows) == 1


class TestSolve:

    def test_init(self):
        team = Team(name="Blue Team 1", color="Blue")
        db.session.add(team)

        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        solve = Solve(host="10.0.0.1", flag=flag, team=team)
        db.session.add(solve)
        db.session.commit()

        assert solve.id is not None
        assert solve.host == "10.0.0.1"
        assert solve.flag_id == flag.id
        assert solve.team_id == team.id

    def test_solve_relationship_to_flag(self):
        """Test that Solve has proper relationship to Flag"""
        team = Team(name="Blue Team 1", color="Blue")
        db.session.add(team)

        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        solve = Solve(host="10.0.0.1", flag=flag, team=team)
        db.session.add(solve)
        db.session.commit()

        # Access solve through flag relationship
        assert len(flag.solves) == 1
        assert flag.solves[0].host == "10.0.0.1"

    def test_solve_relationship_to_team(self):
        """Test that Solve has proper relationship to Team"""
        team = Team(name="Blue Team 1", color="Blue")
        db.session.add(team)

        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        solve = Solve(host="10.0.0.1", flag=flag, team=team)
        db.session.add(solve)
        db.session.commit()

        # Access solve through team relationship
        assert len(team.flag_solves) == 1
        assert team.flag_solves[0].host == "10.0.0.1"

    def test_unique_constraint(self):
        """Test that the unique constraint on (flag_id, host, team_id) works"""
        team = Team(name="Blue Team 1", color="Blue")
        db.session.add(team)

        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        # Create first solve
        solve1 = Solve(host="10.0.0.1", flag=flag, team=team)
        db.session.add(solve1)
        db.session.commit()

        # Try to create duplicate solve with same flag, host, and team
        solve2 = Solve(host="10.0.0.1", flag=flag, team=team)
        db.session.add(solve2)

        # Should raise IntegrityError due to unique constraint
        with pytest.raises(Exception):  # SQLAlchemy IntegrityError
            db.session.commit()

    def test_multiple_solves_different_hosts(self):
        """Test that same flag can be solved from different hosts"""
        team = Team(name="Blue Team 1", color="Blue")
        db.session.add(team)

        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        solve1 = Solve(host="10.0.0.1", flag=flag, team=team)
        solve2 = Solve(host="10.0.0.2", flag=flag, team=team)
        db.session.add(solve1)
        db.session.add(solve2)
        db.session.commit()

        assert len(flag.solves) == 2

    def test_multiple_solves_different_teams(self):
        """Test that same flag can be solved by different teams"""
        team1 = Team(name="Blue Team 1", color="Blue")
        team2 = Team(name="Blue Team 2", color="Blue")
        db.session.add(team1)
        db.session.add(team2)

        start_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        end_time = datetime(2025, 1, 1, 18, 0, 0, tzinfo=pytz.UTC)
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/root/flag.txt"},
            start_time=start_time,
            end_time=end_time,
            perm=Perm.root,
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        solve1 = Solve(host="10.0.0.1", flag=flag, team=team1)
        solve2 = Solve(host="10.0.0.1", flag=flag, team=team2)
        db.session.add(solve1)
        db.session.add(solve2)
        db.session.commit()

        assert len(flag.solves) == 2
        assert len(team1.flag_solves) == 1
        assert len(team2.flag_solves) == 1
